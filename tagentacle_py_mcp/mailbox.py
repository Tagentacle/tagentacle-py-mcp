"""InboxMCP — composable mailbox for any LifecycleNode.

Provides per-topic message buffering, MCP resources (``bus://mailbox/*``),
and ``notifications/resources/updated`` delivery.

Dual access:
  - **MCP layer**: ``subscribe_topic``, ``poll_messages`` tools +
    ``bus://mailbox`` / ``bus://mailbox/{topic}`` resources (for AI agents)
  - **Python layer**: ``push()``, ``drain()``, ``pending`` (for InferenceMux
    or other in-process consumers — zero serialization overhead)

Usage::

    class MyNode(LifecycleNode):
        def __init__(self):
            super().__init__("my_node")
            self.mcp_server = MCPServerComponent("my_node", mcp_port=8100)
            self.mailbox = InboxMCP(self, self.mcp_server.mcp)
"""

import json
import logging
import time
from typing import Annotated, Any, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP
from pydantic import AnyUrl, Field

logger = logging.getLogger("tagentacle.mcp.mailbox")


class InboxMCP:
    """Composable mailbox component — message buffering + MCP resources.

    Registers subscribe/poll tools and ``bus://mailbox`` resources on the
    given FastMCP instance.  Uses the given node's ``subscribe()`` decorator
    for bus callbacks.

    Args:
        node: Any object exposing ``.subscribe(topic)`` decorator and
              ``.subscribers`` dict (i.e. a LifecycleNode).
        mcp:  FastMCP instance to register tools and resources on.
        allowed_topics: Optional topic prefix allow-list for subscribe.
    """

    def __init__(
        self,
        node: Any,
        mcp: FastMCP,
        *,
        allowed_topics: Optional[List[str]] = None,
    ):
        self._node = node
        self._mcp = mcp
        self._allowed_topics = allowed_topics

        # Per-topic message buffer: {topic: [msg_dict, ...]}
        self._subscribed_topics: Dict[str, List[Dict[str, Any]]] = {}
        # Per-topic subscription level: {topic: "trigger" | "silent"}
        self._subscription_levels: Dict[str, str] = {}
        # Active MCP session reference for sending notifications
        self._mcp_session: Any = None

        self._register_tools()
        self._register_resources()

    # ------------------------------------------------------------------
    # Python API (for in-process consumers like InferenceMux)
    # ------------------------------------------------------------------

    @property
    def pending(self) -> int:
        """Total number of unread messages across all topics."""
        return sum(len(msgs) for msgs in self._subscribed_topics.values())

    def pending_for(self, topic: str) -> int:
        """Number of unread messages for a specific topic."""
        return len(self._subscribed_topics.get(topic, []))

    def push(self, topic: str, msg: dict) -> bool:
        """Buffer a message and return True if level is trigger.

        This is called by the bus subscribe callback.  In-process consumers
        can also call it directly for synthetic messages.
        """
        self._subscribed_topics.setdefault(topic, []).append(
            {
                "sender": msg.get("sender"),
                "payload": msg.get("payload"),
                "ts": time.time(),
            }
        )
        return self._subscription_levels.get(topic) == "trigger"

    def drain(
        self, topic: str = "", *, limit: int = 50
    ) -> list[dict] | dict[str, list]:
        """Drain buffered messages (Python API).

        If *topic* is given, returns a list of messages for that topic.
        If *topic* is empty, returns ``{topic: [msgs]}`` for all topics.
        """
        if topic:
            msgs = self._subscribed_topics.get(topic, [])[:limit]
            self._subscribed_topics[topic] = self._subscribed_topics.get(topic, [])[
                limit:
            ]
            return msgs
        else:
            result: Dict[str, list] = {}
            remaining = limit
            for t in list(self._subscribed_topics):
                if remaining <= 0:
                    break
                msgs = self._subscribed_topics[t][:remaining]
                self._subscribed_topics[t] = self._subscribed_topics[t][len(msgs) :]
                if msgs:
                    result[t] = msgs
                    remaining -= len(msgs)
            return result

    @property
    def topics(self) -> list[str]:
        """List of currently subscribed topics."""
        return list(self._subscribed_topics.keys())

    def get_level(self, topic: str) -> str | None:
        """Get the subscription level for a topic, or None if not subscribed."""
        return self._subscription_levels.get(topic)

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        """Register subscribe/poll MCP tools on the FastMCP instance."""

        @self._mcp.tool(
            description=(
                "Subscribe to a Tagentacle bus Topic and start buffering "
                "incoming messages. A resource at bus://mailbox/{topic_path} "
                "will be created. Use poll_messages to read buffered messages."
            ),
        )
        async def subscribe_topic(
            topic: Annotated[str, Field(description="Topic path, e.g. '/chat/output'")],
            level: Annotated[
                str,
                Field(
                    description="'trigger' (notify on message) or 'silent' (buffer only)"
                ),
            ] = "trigger",
            ctx: Context | None = None,
        ) -> str:
            if level not in ("trigger", "silent"):
                raise ValueError(f"Invalid level '{level}'. Use 'trigger' or 'silent'.")

            if topic in self._subscribed_topics:
                n = len(self._subscribed_topics[topic])
                return f"Already subscribed to '{topic}'. {n} buffered message(s)."

            self._subscribed_topics[topic] = []
            self._subscription_levels[topic] = level

            # Capture session for later notification sending
            if ctx is not None:
                try:
                    self._mcp_session = ctx.session
                except Exception:
                    pass

            @self._node.subscribe(topic)
            async def _on_message(msg):
                should_notify = self.push(topic, msg)
                if should_notify:
                    await self._notify_resource_updated(topic)

            # Notify client that resource list changed (new mailbox resource)
            await self._notify_resource_list_changed()

            return (
                f"Subscribed to '{topic}' (level={level}). "
                f"Messages buffered at bus://mailbox{topic}"
            )

        @self._mcp.tool(
            description="Unsubscribe from a Topic and clear its message buffer.",
        )
        async def unsubscribe_topic(
            topic: Annotated[str, Field(description="Topic path to unsubscribe from")],
        ) -> str:
            if topic not in self._subscribed_topics:
                return f"Not subscribed to '{topic}'."
            count = len(self._subscribed_topics.pop(topic, []))
            self._subscription_levels.pop(topic, None)
            self._node.subscribers.pop(topic, None)
            await self._notify_resource_list_changed()
            return f"Unsubscribed from '{topic}'. Cleared {count} buffered message(s)."

        @self._mcp.tool(
            description=(
                "Change subscription level for an already-subscribed topic. "
                "'trigger' sends notifications on new messages; 'silent' buffers only."
            ),
        )
        async def set_subscription_level(
            topic: Annotated[str, Field(description="Topic path")],
            level: Annotated[str, Field(description="'trigger' or 'silent'")],
        ) -> str:
            if level not in ("trigger", "silent"):
                raise ValueError(f"Invalid level '{level}'. Use 'trigger' or 'silent'.")
            if topic not in self._subscribed_topics:
                return f"Not subscribed to '{topic}'. Subscribe first."
            old = self._subscription_levels.get(topic, "trigger")
            self._subscription_levels[topic] = level
            return f"Subscription level for '{topic}': {old} → {level}"

        @self._mcp.tool(
            description=(
                "Read and drain buffered messages from a subscribed topic. "
                "Returns up to `limit` messages and removes them from the buffer."
            ),
        )
        async def poll_messages(
            topic: Annotated[
                str, Field(description="Topic path (omit to poll all)")
            ] = "",
            limit: Annotated[int, Field(description="Max messages to return")] = 50,
        ) -> str:
            if topic:
                if topic not in self._subscribed_topics:
                    return json.dumps({"error": f"Not subscribed to '{topic}'"})
                msgs = self.drain(topic, limit=limit)
                return json.dumps(msgs, ensure_ascii=False, default=str)
            else:
                result = self.drain(limit=limit)
                return json.dumps(result, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # MCP resources
    # ------------------------------------------------------------------

    def _register_resources(self) -> None:
        """Register bus://mailbox resources on the FastMCP instance."""

        @self._mcp.resource(
            "bus://mailbox",
            name="mailbox_overview",
            description="Overview of all subscribed topics with unread message counts.",
            mime_type="application/json",
        )
        def mailbox_overview() -> str:
            overview = {}
            for topic, msgs in self._subscribed_topics.items():
                overview[topic] = {
                    "unread": len(msgs),
                    "level": self._subscription_levels.get(topic, "trigger"),
                }
            return json.dumps(overview, ensure_ascii=False, indent=2)

        @self._mcp.resource(
            "bus://mailbox/{topic_path}",
            name="mailbox_topic",
            description=(
                "Peek at buffered messages for a topic "
                "(non-destructive, use poll_messages to drain)."
            ),
            mime_type="application/json",
        )
        def mailbox_topic(topic_path: str) -> str:
            topic = f"/{topic_path}"
            msgs = self._subscribed_topics.get(topic, [])
            return json.dumps(
                {"topic": topic, "count": len(msgs), "messages": msgs[-20:]},
                ensure_ascii=False,
                default=str,
            )

    # ------------------------------------------------------------------
    # Notification helpers
    # ------------------------------------------------------------------

    async def _notify_resource_updated(self, topic: str) -> None:
        """Best-effort send resource updated notification for a mailbox topic."""
        if self._mcp_session is None:
            return
        try:
            topic_path = topic.lstrip("/")
            uri = AnyUrl(f"bus://mailbox/{topic_path}")
            await self._mcp_session.send_resource_updated(uri=uri)
        except Exception as e:
            logger.debug("Failed to send resource notification for %s: %s", topic, e)

    async def _notify_resource_list_changed(self) -> None:
        """Best-effort notify client that the resource list changed."""
        if self._mcp_session is None:
            return
        try:
            await self._mcp_session.send_resource_list_changed()
        except Exception as e:
            logger.debug("Failed to send resource list changed: %s", e)


# Backward compatibility alias (Q27)
BusMailboxComponent = InboxMCP
