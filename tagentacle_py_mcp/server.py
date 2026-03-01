"""
Tagentacle MCP Server SDK — MCPServerNode base class and TagentacleMCPServer.

This module provides:
  - MCPServerNode: Abstract base class for building MCP Server Nodes in a
    Tagentacle workspace. Inherits LifecycleNode and runs a FastMCP
    Streamable HTTP server, publishing its URL to /mcp/directory on activation.
  - TagentacleMCPServer: Built-in MCP Server that exposes Tagentacle bus
    operations (publish, subscribe, call_service, introspection) as MCP Tools.

Architecture (post-refactor):
  - MCP Server Nodes run their own HTTP endpoint (Streamable HTTP transport).
  - Agent Nodes discover servers via /mcp/directory Topic and connect using
    the native MCP SDK HTTP client — no bus-as-transport intermediary.
  - This enables full MCP protocol support including server→client sampling.

Usage (custom MCP Server Node):
    class WeatherServer(MCPServerNode):
        def __init__(self):
            super().__init__("weather_server", mcp_port=8100)

        def on_configure(self, config):
            super().on_configure(config)

            @self.mcp.tool(description="Get weather for a city")
            def get_weather(city: str) -> str:
                return f"Sunny in {city}"

    async def main():
        node = WeatherServer()
        await node.bringup()
        await node.spin()
"""

import asyncio
import json
import logging
from typing import Any, Annotated, Dict, List, Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from tagentacle_py_core import LifecycleNode

from tagentacle_py_mcp.auth import (
    verify_credential,
    CallerIdentity,
    set_caller_identity,
    check_tool_authorized,
    CredentialInvalid,
    ToolNotAuthorized,
)

logger = logging.getLogger("tagentacle.mcp.server")

# Standard topic for MCP server discovery
MCP_DIRECTORY_TOPIC = "/mcp/directory"


class MCPServerNode(LifecycleNode):
    """Abstract base class for MCP Server Nodes in a Tagentacle workspace.

    Provides:
      - A FastMCP instance for tool/resource/prompt registration.
      - Automatic Streamable HTTP server startup on activation.
      - Automatic /mcp/directory Topic publishing for server discovery.

    Subclasses should register MCP tools in on_configure() using self.mcp
    (the FastMCP instance), then call super().on_configure(config).

    Constructor Args:
        node_id: Tagentacle Node ID for bus communication.
        mcp_name: Human-readable name for the MCP server (default: node_id).
        mcp_port: HTTP port for the Streamable HTTP endpoint.
        mcp_host: HTTP host to bind to (default: "127.0.0.1").
        mcp_path: URL path for the MCP endpoint (default: "/mcp").
        concurrent_sessions: Whether this server supports concurrent sessions.
        description: Human-readable description of this server.
        auth_required: If True, requires a valid JWT Bearer token on every
            HTTP request. The token's ``tool_grants`` are enforced: only
            granted tools are visible in ``list_tools`` and callable.
            The caller's ``agent_id`` is available to tool handlers via
            ``tagentacle_py_mcp.auth.get_caller_identity()``.
    """

    def __init__(
        self,
        node_id: str,
        *,
        mcp_name: Optional[str] = None,
        mcp_port: int = 8000,
        mcp_host: str = "127.0.0.1",
        mcp_path: str = "/mcp",
        concurrent_sessions: bool = True,
        description: str = "",
        auth_required: bool = False,
    ):
        super().__init__(node_id)
        self._mcp_port = mcp_port
        self._mcp_host = mcp_host
        self._mcp_path = mcp_path
        self._concurrent_sessions = concurrent_sessions
        self._server_description = description
        self._auth_required = auth_required
        self._uvicorn_server: Optional[uvicorn.Server] = None
        self._http_task: Optional[asyncio.Task] = None

        # Create FastMCP instance — subclasses register tools on this
        self.mcp = FastMCP(
            name=mcp_name or node_id,
            host=mcp_host,
            port=mcp_port,
            streamable_http_path=mcp_path,
        )

    @property
    def mcp_url(self) -> str:
        """The full URL of this server's MCP endpoint."""
        return f"http://{self._mcp_host}:{self._mcp_port}{self._mcp_path}"

    def on_configure(self, config: Dict[str, Any]):
        """Configure the MCP Server Node.

        Reads mcp_port/mcp_host from bringup config if provided,
        overriding constructor defaults. Subclasses should register
        tools BEFORE calling super().on_configure(config).
        """
        if "mcp_port" in config:
            self._mcp_port = int(config["mcp_port"])
            self.mcp.settings.port = self._mcp_port
        if "mcp_host" in config:
            self._mcp_host = config["mcp_host"]
            self.mcp.settings.host = self._mcp_host

    async def on_activate(self):
        """Start the Streamable HTTP server and publish to /mcp/directory."""
        # Start uvicorn in a background task
        starlette_app = self.mcp.streamable_http_app()

        # Wrap with auth middleware when auth is enabled
        if self._auth_required:
            starlette_app.add_middleware(
                TACLAuthMiddleware, server_id=self.node_id
            )
            logger.info(
                f"MCP Server '{self.node_id}' auth enabled — "
                "JWT Bearer token required."
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

        # Publish server description to /mcp/directory
        await self._publish_directory("available")
        logger.info(
            f"MCP Server '{self.node_id}' active at {self.mcp_url}"
        )

    async def on_deactivate(self):
        """Stop the HTTP server and publish unavailable status."""
        await self._publish_directory("unavailable")
        if self._uvicorn_server:
            self._uvicorn_server.should_exit = True
        if self._http_task:
            try:
                await asyncio.wait_for(self._http_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._http_task.cancel()
        logger.info(f"MCP Server '{self.node_id}' deactivated.")

    async def on_shutdown(self):
        """Ensure HTTP server is stopped on shutdown."""
        if self._uvicorn_server and self._http_task and not self._http_task.done():
            self._uvicorn_server.should_exit = True
            try:
                await asyncio.wait_for(self._http_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._http_task.cancel()

    async def _publish_directory(self, status: str):
        """Publish MCPServerDescription to /mcp/directory Topic."""
        # Gather tool names if available
        tools_summary = []
        try:
            # FastMCP stores tools internally
            tools_summary = list(self.mcp._tool_manager._tools.keys())
        except Exception:
            pass

        description = {
            "server_id": self.node_id,
            "url": self.mcp_url,
            "transport": "streamable-http",
            "concurrent_sessions": self._concurrent_sessions,
            "status": status,
            "source": "node",
            "tools_summary": tools_summary,
            "description": self._server_description,
            "publisher_node_id": self.node_id,
            "auth_required": self._auth_required,
        }
        try:
            await self.publish(MCP_DIRECTORY_TOPIC, description)
        except Exception as e:
            logger.warning(f"Failed to publish to {MCP_DIRECTORY_TOPIC}: {e}")


# ---------------------------------------------------------------------------
# TACL Auth Middleware
# ---------------------------------------------------------------------------

class TACLAuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that enforces JWT Bearer authentication.

    On every request:
      1. Extracts ``Authorization: Bearer <jwt>`` from headers.
      2. Verifies the JWT signature and expiry via ``verify_credential()``.
      3. Sets the ``CallerIdentity`` in a ``contextvars.ContextVar`` so tool
         handlers can read it via ``get_caller_identity()``.

    If authentication fails the request is rejected with HTTP 401.
    """

    def __init__(self, app, server_id: str = ""):
        super().__init__(app)
        self.server_id = server_id

    async def dispatch(self, request: Request, call_next):
        # Extract Bearer token
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing or malformed Authorization header. "
                          "Expected: Bearer <jwt>"},
                status_code=401,
            )

        token = auth_header[7:]  # strip "Bearer "

        try:
            payload = verify_credential(token)
        except CredentialInvalid as exc:
            return JSONResponse(
                {"error": f"Authentication failed: {exc}"},
                status_code=401,
            )

        # Set caller identity for the duration of this request
        identity = CallerIdentity(
            agent_id=payload["agent_id"],
            tool_grants=payload.get("tool_grants", {}),
        )
        set_caller_identity(identity)
        try:
            response = await call_next(request)
        finally:
            set_caller_identity(None)

        return response


class TagentacleMCPServer(MCPServerNode):
    """Built-in MCP Server Node exposing Tagentacle bus operations as MCP Tools.

    Provides 10 MCP tools for bus interaction:
      - publish_to_topic, subscribe_topic, unsubscribe_topic
      - list_nodes, list_topics, list_services
      - get_node_info, describe_topic_schema
      - call_bus_service, ping_daemon

    This allows AI Agents to interact with the entire Tagentacle bus through
    standard MCP tool calls, without needing direct bus protocol knowledge.
    """

    def __init__(
        self,
        node_id: str = "tagentacle_mcp_server",
        *,
        mcp_port: int = 8000,
        allowed_topics: Optional[List[str]] = None,
    ):
        super().__init__(
            node_id,
            mcp_name="tagentacle-mcp-server",
            mcp_port=mcp_port,
            description="Built-in MCP Server exposing Tagentacle bus operations as tools.",
        )
        self.allowed_topics = allowed_topics
        self._subscribed_topics: Dict[str, List[Dict[str, Any]]] = {}
        self.mcp.instructions = (
            "Tagentacle bus interaction server. Use these tools to publish "
            "messages, subscribe to topics, call services, and introspect "
            "the running system."
        )
        self._register_bus_tools()

    def _register_bus_tools(self) -> None:
        """Register all bus interaction tools with FastMCP."""

        # --- pub / sub ---

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

        @self.mcp.tool(
            description=(
                "Subscribe to a Tagentacle bus Topic and start buffering "
                "incoming messages."
            ),
        )
        async def subscribe_topic(
            topic: Annotated[str, Field(description="Topic path, e.g. '/chat/output'")],
        ) -> str:
            if topic in self._subscribed_topics:
                n = len(self._subscribed_topics[topic])
                return f"Already subscribed to '{topic}'. {n} buffered message(s)."

            self._subscribed_topics[topic] = []

            @self.subscribe(topic)
            async def _on_message(msg):
                self._subscribed_topics.setdefault(topic, []).append({
                    "sender": msg.get("sender"),
                    "payload": msg.get("payload"),
                })

            return f"Subscribed to '{topic}'. Messages will be buffered."

        @self.mcp.tool(
            description="Unsubscribe from a Topic and clear its message buffer.",
        )
        async def unsubscribe_topic(
            topic: Annotated[str, Field(description="Topic path to unsubscribe from")],
        ) -> str:
            if topic not in self._subscribed_topics:
                return f"Not subscribed to '{topic}'."
            count = len(self._subscribed_topics.pop(topic, []))
            self.subscribers.pop(topic, None)
            return f"Unsubscribed from '{topic}'. Cleared {count} buffered message(s)."

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
            service: Annotated[str, Field(description="Service name, e.g. '/math/add'")],
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
            result = await self.call_service(
                service, payload or {}, timeout=timeout
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        except asyncio.TimeoutError:
            return f"Error: Daemon did not respond to {service} (timeout)."


