"""Tests for MessageBus."""

from __future__ import annotations

import pytest

from harness.core.message_bus import MessageBus, Message, MessageValidationError


class TestMessageBus:
    def test_send_and_receive(self) -> None:
        bus = MessageBus()
        bus.register_port("agent_b", "input")
        msg = Message(sender="agent_a", receiver="agent_b", port="input", payload={"data": 1})
        bus.send(msg)
        received = bus.receive("agent_b", "input")
        assert len(received) == 1
        assert received[0].payload == {"data": 1}

    def test_receive_clears_queue(self) -> None:
        bus = MessageBus()
        bus.register_port("b", "in")
        bus.send(Message(sender="a", receiver="b", port="in", payload={}))
        bus.receive("b", "in")
        assert bus.receive("b", "in") == []

    def test_peek_does_not_consume(self) -> None:
        bus = MessageBus()
        bus.register_port("b", "in")
        bus.send(Message(sender="a", receiver="b", port="in", payload={"x": 1}))
        peeked = bus.peek("b", "in")
        assert len(peeked) == 1
        received = bus.receive("b", "in")
        assert len(received) == 1

    def test_schema_validation_pass(self) -> None:
        schema = {"required": ["data"]}
        bus = MessageBus()
        bus.register_port("b", "in", schema=schema)
        msg = Message(sender="a", receiver="b", port="in", payload={"data": "hello"})
        bus.send(msg)  # should not raise

    def test_schema_validation_fail(self) -> None:
        schema = {"required": ["data"]}
        bus = MessageBus()
        bus.register_port("b", "in", schema=schema)
        msg = Message(sender="a", receiver="b", port="in", payload={"wrong_key": 1})
        with pytest.raises(MessageValidationError, match="Missing required key"):
            bus.send(msg)

    def test_callable_schema_validator(self) -> None:
        bus = MessageBus()
        bus.register_port("b", "in", schema=lambda p: "data" in p)
        bus.send(Message(sender="a", receiver="b", port="in", payload={"data": 1}))
        with pytest.raises(MessageValidationError):
            bus.send(Message(sender="a", receiver="b", port="in", payload={"nope": 1}))

    def test_history_tracks_all(self) -> None:
        bus = MessageBus()
        bus.send(Message(sender="a", receiver="b", port="in", payload={}))
        bus.send(Message(sender="c", receiver="d", port="out", payload={}))
        assert len(bus.history) == 2

    def test_clear(self) -> None:
        bus = MessageBus()
        bus.send(Message(sender="a", receiver="b", port="in", payload={}))
        bus.clear()
        assert len(bus.history) == 0
        assert bus.receive("b", "in") == []

    def test_unregistered_port_auto_creates(self) -> None:
        bus = MessageBus()
        msg = Message(sender="a", receiver="b", port="in", payload={"x": 1})
        bus.send(msg)  # auto-creates queue
        received = bus.receive("b", "in")
        assert len(received) == 1

    def test_multiple_messages_ordered(self) -> None:
        bus = MessageBus()
        bus.register_port("b", "in")
        for i in range(5):
            bus.send(Message(sender="a", receiver="b", port="in", payload={"i": i}))
        received = bus.receive("b", "in")
        assert [m.payload["i"] for m in received] == [0, 1, 2, 3, 4]
