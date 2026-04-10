"""
Tagentacle MCP Server SDK — MCPServerComponent and BusMCPNode.

This module provides:
  - MCPServerComponent: Composable MCP Server component. Manages FastMCP +
    uvicorn HTTP server + /mcp/directory publishing. Does NOT inherit Node —
    designed for has-a composition with any LifecycleNode.
  - BusMCPNode: Built-in MCP Server that exposes Tagentacle bus
    operations (publish, subscribe, call_service, introspection) as MCP Tools.
    (Previously named TagentacleMCPServer; alias kept for backward compat.)

Architecture:
  - MCP Server is an interface capability, not a Node type.
  - Any Node can compose MCPServerComponent to expose an MCP endpoint.
  - Agent Nodes discover servers via /mcp/directory Topic and connect using
    the native MCP SDK HTTP client — no bus-as-transport intermediary.

Usage (composition pattern — recommended):
    class WeatherServer(LifecycleNode):
        def __init__(self):
            super().__init__("weather_server")
            self.mcp_server = MCPServerComponent(
                "weather_server", mcp_port=8100,
                description="Weather tools",
            )

        def on_configure(self, config):
            @self.mcp_server.mcp.tool(description="Get weather for a city")
            def get_weather(city: str) -> str:
                return f"Sunny in {city}"
            self.mcp_server.configure(config)

        async def on_activate(self):
            await self.mcp_server.start(publish_fn=self.publish)

        async def on_deactivate(self):
            await self.mcp_server.stop(publish_fn=self.publish)

        async def on_shutdown(self):
            await self.mcp_server.shutdown()

    async def main():
        node = WeatherServer()
        await node.bringup()
        await node.spin()
"""

import asyncio
import json
import logging
from typing import Any, Annotated, Callable, Coroutine, Dict, List, Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from tagentacle_py_core import LifecycleNode
from tagentacle_py_mcp.mailbox import InboxMCP
from tagentacle_py_tacl.middleware import TACLAuthMiddleware  # noqa: F401

logger = logging.getLogger("tagentacle.mcp.server")

# Standard topic for MCP server discovery
MCP_DIRECTORY_TOPIC = "/mcp/directory"

# Type alias for publish callback: async (topic, payload) -> None
PublishFn = Callable[[str, dict], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# MCPServerComponent — composable MCP server (no Node inheritance)
# ---------------------------------------------------------------------------


class MCPServerComponent:
    """Composable MCP Server component — manages FastMCP + uvicorn + directory.

    Does NOT inherit Node. Designed for has-a composition with any
    LifecycleNode (or standalone use).

    Provides:
      - A FastMCP instance for tool/resource/prompt registration (``self.mcp``).
      - ``start()`` / ``stop()`` for uvicorn HTTP server lifecycle.
      - Optional ``/mcp/directory`` Topic publishing via ``publish_fn``.
      - Optional TACL auth middleware.

    Constructor Args:
        server_id: Identifier for this server (used in directory publishing).
        mcp_name: Human-readable name for the MCP server (default: server_id).
        mcp_port: HTTP port for the Streamable HTTP endpoint.
        mcp_host: HTTP host to bind to (default: "127.0.0.1").
        mcp_path: URL path for the MCP endpoint (default: "/mcp").
        concurrent_sessions: Whether this server supports concurrent sessions.
        description: Human-readable description of this server.
        auth_required: If True, mounts TACLAuthMiddleware requiring JWT Bearer.
    """

    def __init__(
        self,
        server_id: str,
        *,
        mcp_name: Optional[str] = None,
        mcp_port: int = 8000,
        mcp_host: str = "127.0.0.1",
        mcp_path: str = "/mcp",
        concurrent_sessions: bool = True,
        description: str = "",
        instructions: Optional[str] = None,
        auth_required: bool = False,
    ):
        self.server_id = server_id
        self._mcp_port = mcp_port
        self._mcp_host = mcp_host
        self._mcp_path = mcp_path
        self._concurrent_sessions = concurrent_sessions
        self._description = description
        self._auth_required = auth_required
        self._uvicorn_server: Optional[uvicorn.Server] = None
        self._http_task: Optional[asyncio.Task] = None

        # FastMCP instance — register tools/resources/prompts on this
        self.mcp = FastMCP(
            name=mcp_name or server_id,
            instructions=instructions,
            host=mcp_host,
            port=mcp_port,
            streamable_http_path=mcp_path,
        )

    @property
    def mcp_url(self) -> str:
        """The full URL of this server's MCP endpoint."""
        return f"http://{self._mcp_host}:{self._mcp_port}{self._mcp_path}"

    def configure(self, config: Dict[str, Any]) -> None:
        """Read mcp_port/mcp_host from config dict if present."""
        if "mcp_port" in config:
            self._mcp_port = int(config["mcp_port"])
            self.mcp.settings.port = self._mcp_port
        if "mcp_host" in config:
            self._mcp_host = config["mcp_host"]
            self.mcp.settings.host = self._mcp_host

    async def start(self, *, publish_fn: Optional[PublishFn] = None) -> None:
        """Start the uvicorn HTTP server.

        If ``publish_fn`` is provided, publishes an "available" entry to
        ``/mcp/directory`` after the server is listening.
        """
        starlette_app = self.mcp.streamable_http_app()

        if self._auth_required:
            starlette_app.add_middleware(TACLAuthMiddleware, server_id=self.server_id)
            logger.info(
                "MCP Server '%s' auth enabled — JWT Bearer token required.",
                self.server_id,
            )

        config = uvicorn.Config(
            starlette_app,
            host=self._mcp_host,
            port=self._mcp_port,
            log_level="warning",
        )
        self._uvicorn_server = uvicorn.Server(config)
        self._http_task = asyncio.create_task(self._uvicorn_server.serve())

        # Give uvicorn a moment to bind
        await asyncio.sleep(0.3)

        if publish_fn:
            await self._publish_directory("available", publish_fn)
        logger.info("MCP Server '%s' active at %s", self.server_id, self.mcp_url)

    async def stop(self, *, publish_fn: Optional[PublishFn] = None) -> None:
        """Stop the uvicorn HTTP server.

        If ``publish_fn`` is provided, publishes an "unavailable" entry to
        ``/mcp/directory`` before shutting down.
        """
        if publish_fn:
            await self._publish_directory("unavailable", publish_fn)
        if self._uvicorn_server:
            self._uvicorn_server.should_exit = True
        if self._http_task:
            try:
                await asyncio.wait_for(self._http_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._http_task.cancel()
        logger.info("MCP Server '%s' stopped.", self.server_id)

    async def shutdown(self) -> None:
        """Ensure the HTTP server is stopped (idempotent)."""
        if self._uvicorn_server and self._http_task and not self._http_task.done():
            self._uvicorn_server.should_exit = True
            try:
                await asyncio.wait_for(self._http_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._http_task.cancel()

    def directory_entry(self, status: str = "available") -> dict:
        """Return an MCPServerDescription dict for /mcp/directory publishing.

        Schema: mcp-interfaces/msg/MCPServerDescription.json
        See: https://tagentacle.dev/schemas/MCPServerDescription.json
        """
        tools_summary: list[str] = []
        try:
            tools_summary = list(self.mcp._tool_manager._tools.keys())
        except Exception:
            pass

        return {
            "server_id": self.server_id,
            "url": self.mcp_url,
            "transport": "streamable-http",
            "concurrent_sessions": self._concurrent_sessions,
            "status": status,
            "source": "node",
            "tools_summary": tools_summary,
            "description": self._description,
            "publisher_node_id": self.server_id,
            "auth_required": self._auth_required,
        }

    async def _publish_directory(self, status: str, publish_fn: PublishFn) -> None:
        """Publish MCPServerDescription to /mcp/directory via callback."""
        try:
            await publish_fn(MCP_DIRECTORY_TOPIC, self.directory_entry(status))
        except Exception as e:
            logger.warning("Failed to publish to %s: %s", MCP_DIRECTORY_TOPIC, e)


class BusMCPNode(LifecycleNode):
    """Built-in MCP Server exposing Tagentacle bus operations as MCP Tools.

    Uses LifecycleNode + MCPServerComponent + InboxMCP (composition).

    Provides MCP tools for bus interaction:
      - publish_to_topic (direct)
      - subscribe_topic, unsubscribe_topic, set_subscription_level,
        poll_messages (via InboxMCP)
      - list_nodes, list_topics, list_services, get_node_info,
        describe_topic_schema, call_bus_service, ping_daemon (direct)

    Subscribed topics are exposed as MCP resources (``bus://mailbox/{topic}``).
    When level="trigger", new messages send ``notifications/resources/updated``.

    This allows AI Agents to interact with the entire Tagentacle bus through
    standard MCP tool calls, without needing direct bus protocol knowledge.

    Previously named TagentacleMCPServer. Alias kept for backward compatibility.
    """

    def __init__(
        self,
        node_id: str = "tagentacle_mcp_server",
        *,
        mcp_port: int = 8000,
        allowed_topics: Optional[List[str]] = None,
    ):
        super().__init__(node_id)
        self.mcp_server = MCPServerComponent(
            server_id=node_id,
            mcp_name="bus-mcp-server",
            mcp_port=mcp_port,
            description="Built-in MCP Server exposing Tagentacle bus operations as tools.",
            instructions=(
                "Tagentacle bus interaction server. Use these tools to publish "
                "messages, subscribe to topics, call services, and introspect "
                "the running system. Subscribed topics appear as resources at "
                "bus://mailbox/{topic_path}."
            ),
        )
        # Convenience alias — tools are registered on the FastMCP instance
        self.mcp = self.mcp_server.mcp
        self.allowed_topics = allowed_topics

        # Mailbox component — subscribe/poll tools + bus://mailbox resources
        self.mailbox = InboxMCP(self, self.mcp, allowed_topics=allowed_topics)

        self._register_bus_tools()

    @property
    def mcp_url(self) -> str:
        """The full URL of this server's MCP endpoint."""
        return self.mcp_server.mcp_url

    def on_configure(self, config: Dict[str, Any]):
        """Configure MCP port/host from bringup config."""
        self.mcp_server.configure(config)

    async def on_activate(self):
        """Start the MCP HTTP server and publish to /mcp/directory."""
        await self.mcp_server.start(publish_fn=self.publish)

    async def on_deactivate(self):
        """Stop the HTTP server and publish unavailable status."""
        await self.mcp_server.stop(publish_fn=self.publish)

    async def on_shutdown(self):
        """Ensure HTTP server is stopped."""
        await self.mcp_server.shutdown()

    def _register_bus_tools(self) -> None:
        """Register publish, introspection, RPC, and health tools."""

        # --- publish ---

        @self.mcp.tool(
            description=(
                "Publish a JSON message to a Tagentacle bus Topic. "
                "Other nodes subscribed to that topic will receive the message."
            ),
        )
        async def publish_to_topic(
            topic: Annotated[str, Field(description="Topic path, e.g. '/chat/input'")],
            payload: Annotated[dict, Field(description="JSON payload to publish")],
        ) -> str:
            if self.allowed_topics is not None:
                if not any(topic.startswith(p) for p in self.allowed_topics):
                    raise ValueError(
                        f"Topic '{topic}' not in allow-list: {self.allowed_topics}"
                    )
            await self.publish(topic, payload)
            return f"Published to '{topic}' successfully."

        # --- introspection ---

        @self.mcp.tool(
            description="List all nodes currently connected to the Tagentacle Daemon.",
        )
        async def list_nodes() -> str:
            return await self._daemon_query("/tagentacle/list_nodes")

        @self.mcp.tool(
            description="List all active Topics on the Tagentacle bus.",
        )
        async def list_topics() -> str:
            return await self._daemon_query("/tagentacle/list_topics")

        @self.mcp.tool(
            description="List all registered Services on the Tagentacle bus.",
        )
        async def list_services() -> str:
            return await self._daemon_query("/tagentacle/list_services")

        @self.mcp.tool(
            description="Get detailed information about a specific node.",
        )
        async def get_node_info(
            node_id: Annotated[str, Field(description="The node_id to query")],
        ) -> str:
            return await self._daemon_query(
                "/tagentacle/get_node_info", {"node_id": node_id}
            )

        @self.mcp.tool(
            description=(
                "Get the JSON Schema definition for a Topic's message format. "
                "Useful for understanding payload structure before publishing."
            ),
        )
        async def describe_topic_schema(
            topic: Annotated[str, Field(description="Topic path to query schema for")],
        ) -> str:
            return await self._daemon_query(
                "/tagentacle/describe_topic_schema", {"topic": topic}
            )

        # --- generic service call ---

        @self.mcp.tool(
            description="Call any Service on the Tagentacle bus via RPC and return the response.",
        )
        async def call_bus_service(
            service: Annotated[
                str, Field(description="Service name, e.g. '/math/add'")
            ],
            payload: Annotated[dict, Field(description="Request payload")],
            timeout: Annotated[float, Field(description="Timeout in seconds")] = 30.0,
        ) -> str:
            try:
                result = await self.call_service(service, payload, timeout=timeout)
                return json.dumps(result, ensure_ascii=False, indent=2)
            except asyncio.TimeoutError:
                return f"Error: Service '{service}' did not respond within {timeout}s."

        # --- health ---

        @self.mcp.tool(
            description="Check if the Tagentacle Daemon is healthy and responsive.",
        )
        async def ping_daemon() -> str:
            return await self._daemon_query("/tagentacle/ping", timeout=5.0)

    async def _daemon_query(
        self, service: str, payload: Optional[dict] = None, *, timeout: float = 10.0
    ) -> str:
        """Call a Daemon introspection service and return pretty-printed JSON."""
        try:
            result = await self.call_service(service, payload or {}, timeout=timeout)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except asyncio.TimeoutError:
            return f"Error: Daemon did not respond to {service} (timeout)."


# ---------------------------------------------------------------------------
# Backward-compatibility alias
# ---------------------------------------------------------------------------


def _deprecated_alias():
    import warnings

    warnings.warn(
        "TagentacleMCPServer is deprecated, use BusMCPNode instead.",
        DeprecationWarning,
        stacklevel=3,
    )
    return BusMCPNode


TagentacleMCPServer = BusMCPNode  # direct alias, no warning on import
# To emit warnings on instantiation, downstream can check class.__name__.

# Backward compatibility alias (Q27)
BusMCPServer = BusMCPNode
