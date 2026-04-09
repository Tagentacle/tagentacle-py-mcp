"""Tests for BusMailboxComponent Python API (in-process access)."""

from unittest.mock import MagicMock

from mcp.server.fastmcp import FastMCP

from tagentacle_py_mcp.mailbox import BusMailboxComponent


def _make_mailbox() -> tuple:
    """Create a BusMailboxComponent with a mock node and real FastMCP."""
    node = MagicMock()
    node.subscribers = {}

    # Make node.subscribe() a decorator that registers callbacks
    def fake_subscribe(topic):
        def decorator(fn):
            node.subscribers.setdefault(topic, []).append(fn)
            return fn

        return decorator

    node.subscribe = fake_subscribe

    mcp = FastMCP("test")
    mailbox = BusMailboxComponent(node, mcp)
    return mailbox, node, mcp


class TestPythonAPI:
    """Tests for the Python-level API (push, drain, pending)."""

    def test_push_returns_true_for_trigger(self):
        mailbox, _, _ = _make_mailbox()
        mailbox._subscribed_topics["/chat"] = []
        mailbox._subscription_levels["/chat"] = "trigger"

        result = mailbox.push("/chat", {"sender": "a", "payload": {"text": "hi"}})
        assert result is True
        assert mailbox.pending == 1

    def test_push_returns_false_for_silent(self):
        mailbox, _, _ = _make_mailbox()
        mailbox._subscribed_topics["/bg"] = []
        mailbox._subscription_levels["/bg"] = "silent"

        result = mailbox.push("/bg", {"sender": "a", "payload": {}})
        assert result is False
        assert mailbox.pending == 1

    def test_drain_single_topic(self):
        mailbox, _, _ = _make_mailbox()
        mailbox._subscribed_topics["/t"] = [
            {"sender": "a", "payload": {}, "ts": 1.0},
            {"sender": "b", "payload": {}, "ts": 2.0},
        ]

        msgs = mailbox.drain("/t")
        assert len(msgs) == 2
        assert mailbox.pending_for("/t") == 0

    def test_drain_respects_limit(self):
        mailbox, _, _ = _make_mailbox()
        mailbox._subscribed_topics["/t"] = [
            {"sender": "x", "payload": {}, "ts": float(i)} for i in range(10)
        ]

        msgs = mailbox.drain("/t", limit=3)
        assert len(msgs) == 3
        assert mailbox.pending_for("/t") == 7

    def test_drain_all_topics(self):
        mailbox, _, _ = _make_mailbox()
        mailbox._subscribed_topics["/a"] = [{"sender": "x", "payload": {}, "ts": 1.0}]
        mailbox._subscribed_topics["/b"] = [{"sender": "y", "payload": {}, "ts": 2.0}]

        result = mailbox.drain()
        assert "/a" in result
        assert "/b" in result
        assert mailbox.pending == 0

    def test_pending_and_pending_for(self):
        mailbox, _, _ = _make_mailbox()
        mailbox._subscribed_topics["/a"] = [{"x": 1}, {"x": 2}]
        mailbox._subscribed_topics["/b"] = [{"x": 3}]

        assert mailbox.pending == 3
        assert mailbox.pending_for("/a") == 2
        assert mailbox.pending_for("/b") == 1
        assert mailbox.pending_for("/c") == 0

    def test_topics_property(self):
        mailbox, _, _ = _make_mailbox()
        mailbox._subscribed_topics["/a"] = []
        mailbox._subscribed_topics["/b"] = []

        assert set(mailbox.topics) == {"/a", "/b"}

    def test_get_level(self):
        mailbox, _, _ = _make_mailbox()
        mailbox._subscribed_topics["/a"] = []
        mailbox._subscription_levels["/a"] = "silent"

        assert mailbox.get_level("/a") == "silent"
        assert mailbox.get_level("/missing") is None


class TestMailboxComponentStandalone:
    """Test BusMailboxComponent used independently (not via BusMCPServer)."""

    def test_tools_registered(self):
        _, _, mcp = _make_mailbox()
        tools = list(mcp._tool_manager._tools.keys())
        assert "subscribe_topic" in tools
        assert "unsubscribe_topic" in tools
        assert "set_subscription_level" in tools
        assert "poll_messages" in tools

    def test_resources_registered(self):
        _, _, mcp = _make_mailbox()
        assert "bus://mailbox" in mcp._resource_manager._resources
        assert "bus://mailbox/{topic_path}" in mcp._resource_manager._templates
