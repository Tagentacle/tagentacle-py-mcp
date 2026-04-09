"""Tests for BusMCPServer subscribe levels, resources, and notifications."""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from tagentacle_py_mcp.server import BusMCPServer, MCPServerComponent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bus_server(node_id: str = "test_bus") -> BusMCPServer:
    """Create a BusMCPServer with connections mocked out."""
    server = BusMCPServer(node_id, mcp_port=9999)
    # Simulate connected state so subscribe() decorator works
    server._connected = True
    server._send_json = AsyncMock()
    return server


def _fake_context(session=None):
    """Return a mock MCP Context with an optional session."""
    ctx = MagicMock()
    ctx.session = session or AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# Subscription levels
# ---------------------------------------------------------------------------


class TestSubscribeTopic:
    """Tests for the subscribe_topic tool."""

    @pytest.mark.asyncio
    async def test_subscribe_default_level_is_trigger(self):
        server = _make_bus_server()
        ctx = _fake_context()

        # Access the registered tool function
        tool_fn = server.mcp._tool_manager._tools["subscribe_topic"].fn
        result = await tool_fn(topic="/chat/output", ctx=ctx)

        assert "/chat/output" in server.mailbox._subscribed_topics
        assert server.mailbox._subscription_levels["/chat/output"] == "trigger"
        assert "level=trigger" in result

    @pytest.mark.asyncio
    async def test_subscribe_silent_level(self):
        server = _make_bus_server()
        ctx = _fake_context()

        tool_fn = server.mcp._tool_manager._tools["subscribe_topic"].fn
        result = await tool_fn(topic="/sensors/temp", level="silent", ctx=ctx)

        assert server.mailbox._subscription_levels["/sensors/temp"] == "silent"
        assert "level=silent" in result

    @pytest.mark.asyncio
    async def test_subscribe_invalid_level_raises(self):
        server = _make_bus_server()
        ctx = _fake_context()

        tool_fn = server.mcp._tool_manager._tools["subscribe_topic"].fn
        with pytest.raises(ValueError, match="Invalid level"):
            await tool_fn(topic="/x", level="unknown", ctx=ctx)

    @pytest.mark.asyncio
    async def test_subscribe_duplicate_returns_buffered_count(self):
        server = _make_bus_server()
        ctx = _fake_context()
        server.mailbox._subscribed_topics["/dup"] = [{"payload": "a"}]
        server.mailbox._subscription_levels["/dup"] = "trigger"

        tool_fn = server.mcp._tool_manager._tools["subscribe_topic"].fn
        result = await tool_fn(topic="/dup", ctx=ctx)

        assert "Already subscribed" in result
        assert "1 buffered" in result

    @pytest.mark.asyncio
    async def test_subscribe_captures_session(self):
        server = _make_bus_server()
        session = AsyncMock()
        ctx = _fake_context(session=session)

        tool_fn = server.mcp._tool_manager._tools["subscribe_topic"].fn
        await tool_fn(topic="/test", ctx=ctx)

        assert server.mailbox._mcp_session is session


class TestSetSubscriptionLevel:
    """Tests for the set_subscription_level tool."""

    @pytest.mark.asyncio
    async def test_change_level(self):
        server = _make_bus_server()
        server.mailbox._subscribed_topics["/chat"] = []
        server.mailbox._subscription_levels["/chat"] = "trigger"

        tool_fn = server.mcp._tool_manager._tools["set_subscription_level"].fn
        result = await tool_fn(topic="/chat", level="silent")

        assert server.mailbox._subscription_levels["/chat"] == "silent"
        assert "trigger → silent" in result

    @pytest.mark.asyncio
    async def test_change_level_not_subscribed(self):
        server = _make_bus_server()

        tool_fn = server.mcp._tool_manager._tools["set_subscription_level"].fn
        result = await tool_fn(topic="/nope", level="trigger")

        assert "Not subscribed" in result

    @pytest.mark.asyncio
    async def test_change_level_invalid_raises(self):
        server = _make_bus_server()
        server.mailbox._subscribed_topics["/x"] = []
        server.mailbox._subscription_levels["/x"] = "trigger"

        tool_fn = server.mcp._tool_manager._tools["set_subscription_level"].fn
        with pytest.raises(ValueError, match="Invalid level"):
            await tool_fn(topic="/x", level="bad")


class TestUnsubscribeTopic:
    """Tests for subscribe → unsubscribe flow."""

    @pytest.mark.asyncio
    async def test_unsubscribe_clears_buffer_and_level(self):
        server = _make_bus_server()
        server.mailbox._subscribed_topics["/topic"] = [{"payload": "msg"}]
        server.mailbox._subscription_levels["/topic"] = "trigger"
        server.subscribers["/topic"] = [lambda m: None]

        tool_fn = server.mcp._tool_manager._tools["unsubscribe_topic"].fn
        result = await tool_fn(topic="/topic")

        assert "/topic" not in server.mailbox._subscribed_topics
        assert "/topic" not in server.mailbox._subscription_levels
        assert "Cleared 1" in result

    @pytest.mark.asyncio
    async def test_unsubscribe_not_subscribed(self):
        server = _make_bus_server()

        tool_fn = server.mcp._tool_manager._tools["unsubscribe_topic"].fn
        result = await tool_fn(topic="/missing")

        assert "Not subscribed" in result


# ---------------------------------------------------------------------------
# poll_messages
# ---------------------------------------------------------------------------


class TestPollMessages:
    """Tests for the poll_messages tool."""

    @pytest.mark.asyncio
    async def test_poll_single_topic(self):
        server = _make_bus_server()
        server.mailbox._subscribed_topics["/chat"] = [
            {"sender": "a", "payload": {"text": "hi"}, "ts": 1.0},
            {"sender": "b", "payload": {"text": "hey"}, "ts": 2.0},
        ]

        tool_fn = server.mcp._tool_manager._tools["poll_messages"].fn
        result = await tool_fn(topic="/chat", limit=10)
        msgs = json.loads(result)

        assert len(msgs) == 2
        assert msgs[0]["payload"]["text"] == "hi"
        # Buffer should be drained
        assert server.mailbox._subscribed_topics["/chat"] == []

    @pytest.mark.asyncio
    async def test_poll_respects_limit(self):
        server = _make_bus_server()
        server.mailbox._subscribed_topics["/t"] = [
            {"sender": "x", "payload": {}, "ts": float(i)} for i in range(10)
        ]

        tool_fn = server.mcp._tool_manager._tools["poll_messages"].fn
        result = await tool_fn(topic="/t", limit=3)
        msgs = json.loads(result)

        assert len(msgs) == 3
        assert len(server.mailbox._subscribed_topics["/t"]) == 7

    @pytest.mark.asyncio
    async def test_poll_all_topics(self):
        server = _make_bus_server()
        server.mailbox._subscribed_topics["/a"] = [
            {"sender": "x", "payload": {}, "ts": 1.0}
        ]
        server.mailbox._subscribed_topics["/b"] = [
            {"sender": "y", "payload": {}, "ts": 2.0}
        ]

        tool_fn = server.mcp._tool_manager._tools["poll_messages"].fn
        result = await tool_fn(topic="", limit=50)
        data = json.loads(result)

        assert "/a" in data
        assert "/b" in data

    @pytest.mark.asyncio
    async def test_poll_not_subscribed(self):
        server = _make_bus_server()

        tool_fn = server.mcp._tool_manager._tools["poll_messages"].fn
        result = await tool_fn(topic="/missing", limit=10)
        data = json.loads(result)

        assert "error" in data


# ---------------------------------------------------------------------------
# Mailbox resources
# ---------------------------------------------------------------------------


class TestMailboxResources:
    """Tests for bus://mailbox resources."""

    def test_mailbox_overview_empty(self):
        server = _make_bus_server()

        resource_fn = server.mcp._resource_manager._resources["bus://mailbox"].fn
        result = json.loads(resource_fn())

        assert result == {}

    def test_mailbox_overview_with_topics(self):
        server = _make_bus_server()
        server.mailbox._subscribed_topics["/a"] = [{"x": 1}, {"x": 2}]
        server.mailbox._subscribed_topics["/b"] = []
        server.mailbox._subscription_levels["/a"] = "trigger"
        server.mailbox._subscription_levels["/b"] = "silent"

        resource_fn = server.mcp._resource_manager._resources["bus://mailbox"].fn
        result = json.loads(resource_fn())

        assert result["/a"]["unread"] == 2
        assert result["/a"]["level"] == "trigger"
        assert result["/b"]["unread"] == 0
        assert result["/b"]["level"] == "silent"

    def test_mailbox_topic_messages(self):
        server = _make_bus_server()
        server.mailbox._subscribed_topics["/chat/out"] = [
            {"sender": "a", "payload": {"text": "hello"}, "ts": 1.0},
        ]

        # Resource templates are stored in _templates, not _resources
        resource_fn = server.mcp._resource_manager._templates[
            "bus://mailbox/{topic_path}"
        ].fn
        result = json.loads(resource_fn(topic_path="chat/out"))

        assert result["topic"] == "/chat/out"
        assert result["count"] == 1
        assert len(result["messages"]) == 1

    def test_mailbox_topic_not_subscribed(self):
        server = _make_bus_server()

        resource_fn = server.mcp._resource_manager._templates[
            "bus://mailbox/{topic_path}"
        ].fn
        result = json.loads(resource_fn(topic_path="unknown"))

        assert result["count"] == 0
        assert result["messages"] == []


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class TestNotifications:
    """Tests for resource notification helpers."""

    @pytest.mark.asyncio
    async def test_notify_resource_updated_sends_to_session(self):
        server = _make_bus_server()
        session = AsyncMock()
        server.mailbox._mcp_session = session

        await server.mailbox._notify_resource_updated("/chat/out")

        session.send_resource_updated.assert_called_once()
        call_uri = session.send_resource_updated.call_args.kwargs["uri"]
        assert str(call_uri) == "bus://mailbox/chat/out"

    @pytest.mark.asyncio
    async def test_notify_resource_updated_no_session(self):
        server = _make_bus_server()
        server.mailbox._mcp_session = None

        # Should not raise
        await server.mailbox._notify_resource_updated("/test")

    @pytest.mark.asyncio
    async def test_notify_resource_updated_session_error(self):
        server = _make_bus_server()
        session = AsyncMock()
        session.send_resource_updated.side_effect = RuntimeError("disconnected")
        server.mailbox._mcp_session = session

        # Should not raise — best effort
        await server.mailbox._notify_resource_updated("/topic")

    @pytest.mark.asyncio
    async def test_notify_resource_list_changed(self):
        server = _make_bus_server()
        session = AsyncMock()
        server.mailbox._mcp_session = session

        await server.mailbox._notify_resource_list_changed()

        session.send_resource_list_changed.assert_called_once()

    @pytest.mark.asyncio
    async def test_notify_resource_list_changed_no_session(self):
        server = _make_bus_server()
        server.mailbox._mcp_session = None

        await server.mailbox._notify_resource_list_changed()


# ---------------------------------------------------------------------------
# Bus callback integration
# ---------------------------------------------------------------------------


class TestBusCallbackIntegration:
    """Test that bus messages are buffered and trigger notifications."""

    @pytest.mark.asyncio
    async def test_message_buffered_on_callback(self):
        server = _make_bus_server()
        ctx = _fake_context()

        # Subscribe via tool
        tool_fn = server.mcp._tool_manager._tools["subscribe_topic"].fn
        await tool_fn(topic="/data", level="trigger", ctx=ctx)

        # Simulate bus message delivery via the registered subscriber callback
        assert "/data" in server.subscribers
        callback = server.subscribers["/data"][0]

        msg = {"sender": "node_a", "payload": {"value": 42}}
        await callback(msg)

        assert len(server.mailbox._subscribed_topics["/data"]) == 1
        buffered = server.mailbox._subscribed_topics["/data"][0]
        assert buffered["sender"] == "node_a"
        assert buffered["payload"]["value"] == 42
        assert "ts" in buffered

    @pytest.mark.asyncio
    async def test_trigger_level_sends_notification(self):
        server = _make_bus_server()
        session = AsyncMock()
        ctx = _fake_context(session=session)

        tool_fn = server.mcp._tool_manager._tools["subscribe_topic"].fn
        await tool_fn(topic="/events", level="trigger", ctx=ctx)

        callback = server.subscribers["/events"][0]
        await callback({"sender": "x", "payload": {}})

        session.send_resource_updated.assert_called_once()

    @pytest.mark.asyncio
    async def test_silent_level_no_notification(self):
        server = _make_bus_server()
        session = AsyncMock()
        ctx = _fake_context(session=session)

        tool_fn = server.mcp._tool_manager._tools["subscribe_topic"].fn
        await tool_fn(topic="/bg", level="silent", ctx=ctx)

        callback = server.subscribers["/bg"][0]
        await callback({"sender": "x", "payload": {}})

        # Message buffered but no notification
        assert len(server.mailbox._subscribed_topics["/bg"]) == 1
        session.send_resource_updated.assert_not_called()


# ---------------------------------------------------------------------------
# MCPServerComponent basic tests
# ---------------------------------------------------------------------------


class TestMCPServerComponent:
    """Basic tests for MCPServerComponent."""

    def test_mcp_url(self):
        comp = MCPServerComponent("test", mcp_port=8200)
        assert comp.mcp_url == "http://127.0.0.1:8200/mcp"

    def test_configure_port(self):
        comp = MCPServerComponent("test", mcp_port=8000)
        comp.configure({"mcp_port": "9000"})
        assert comp._mcp_port == 9000
        assert "9000" in comp.mcp_url

    def test_directory_entry(self):
        comp = MCPServerComponent("my_server", mcp_port=8100, description="Test server")
        entry = comp.directory_entry("available")
        assert entry["server_id"] == "my_server"
        assert entry["status"] == "available"
        assert entry["description"] == "Test server"
        assert entry["transport"] == "streamable-http"
