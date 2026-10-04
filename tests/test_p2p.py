"""Tests for the reusable :mod:`p2p` toolkit.

These tests only exercise the generic messaging layer - no faxes, no Flet. They
use loopback sockets on an OS-assigned port (``port=0``) so they are fast and
never clash with a real service.
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct

import pytest

from p2p import (
    DISCOVERY_MAGIC,
    MAGIC,
    Message,
    PeerDiscovery,
    PeerInfo,
    PeerServer,
    encode,
    read_message,
    send,
)
from p2p.errors import P2PConnectionError, P2PProtocolError, P2PPayloadTooLarge
from p2p.message import DEFAULT_MAX_PAYLOAD_BYTES


def _frame(raw_header: dict, data: bytes = b"") -> bytes:
    """Hand-build a frame so we can feed deliberately malformed input."""
    header = json.dumps(raw_header, separators=(",", ":")).encode("utf-8")
    return MAGIC + struct.pack("!I", len(header)) + header + data


def _reader_with(payload: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


async def test_encode_then_read_round_trip():
    original = Message(kind="chat", data=b"\x00\x01\x02", meta={"lang": "en"})
    message = await read_message(_reader_with(encode(original)))
    assert message is not None
    assert message.kind == "chat"
    assert message.data == b"\x00\x01\x02"
    assert message.meta == {"lang": "en"}


async def test_clean_eof_returns_none():
    # An empty stream is a polite "we're done", not an error.
    assert await read_message(_reader_with(b"")) is None


async def test_bad_magic_is_rejected():
    with pytest.raises(P2PProtocolError):
        await read_message(_reader_with(b"XXXX\x00\x00\x00\x02{}"))


async def test_oversized_declared_payload_is_rejected():
    frame = _frame({"kind": "big", "len": DEFAULT_MAX_PAYLOAD_BYTES + 1})
    with pytest.raises(P2PPayloadTooLarge):
        await read_message(_reader_with(frame))


async def test_truncated_frame_is_rejected():
    # Says the payload is 4 bytes, but only provides 2.
    frame = _frame({"kind": "short", "len": 4}, data=b"ab")
    with pytest.raises(P2PProtocolError):
        await read_message(_reader_with(frame))


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    """Tiny polling helper: sockets are asynchronous, so give the server a beat."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before the timeout")


async def test_loopback_send_and_receive():
    received: list[Message] = []
    server = PeerServer(port=0, on_message=lambda peer, msg: received.append(msg))
    port = await server.start()
    try:
        await send(
            "127.0.0.1",
            port,
            [Message("first", b"1"), Message("second", b"2", {"n": 2})],
        )
        await _wait_for(lambda: len(received) == 2)
    finally:
        await server.stop()

    assert [m.kind for m in received] == ["first", "second"]
    assert received[1].data == b"2"
    assert received[1].meta == {"n": 2}


async def test_streaming_async_generator_is_ordered():
    received: list[Message] = []
    server = PeerServer(port=0, on_message=lambda peer, msg: received.append(msg))
    port = await server.start()

    async def frames():
        for i in range(5):
            yield Message("line", str(i).encode())
            await asyncio.sleep(0)  # yield control, like a paced stream

    try:
        await send("127.0.0.1", port, frames())
        await _wait_for(lambda: len(received) == 5)
    finally:
        await server.stop()

    assert [m.data for m in received] == [b"0", b"1", b"2", b"3", b"4"]


async def test_connection_refused_is_wrapped():
    # Start and immediately stop a server to get a port nobody is listening on.
    server = PeerServer(port=0)
    port = await server.start()
    await server.stop()

    with pytest.raises(P2PConnectionError):
        await send("127.0.0.1", port, Message("hi"), connect_timeout=2.0)


async def test_async_and_sync_callbacks_both_work():
    order: list[str] = []

    async def on_message(peer, msg):
        order.append("message")

    def on_connect(peer):
        order.append("connect")

    server = PeerServer(port=0, on_message=on_message, on_connect=on_connect)
    port = await server.start()
    try:
        await send("127.0.0.1", port, Message("x"))
        await _wait_for(lambda: "message" in order)
    finally:
        await server.stop()
    assert order[0] == "connect"


# ---------------------------------------------------------------- discovery
def _beacon(service: str, instance_id: str, name: str, port: int) -> bytes:
    return json.dumps(
        {
            "magic": DISCOVERY_MAGIC,
            "id": instance_id,
            "service": service,
            "name": name,
            "port": port,
        }
    ).encode("utf-8")


def test_discovery_tracks_peers_but_ignores_own_and_foreign_beacons():
    seen: list[PeerInfo] = []
    discovery = PeerDiscovery("faxer", port=9100, name="A", on_peer=seen.append)

    # A beacon from another instance of the same service is tracked.
    discovery._on_datagram(
        _beacon("faxer", "other", "B", 9100), ("192.168.1.9", 5555)
    )
    assert [p.address for p in discovery.peers] == ["192.168.1.9:9100"]
    assert len(seen) == 1

    # Our own beacon (same instance id) is ignored.
    discovery._on_datagram(
        _beacon("faxer", discovery.instance_id, "A", 9100), ("192.168.1.9", 5555)
    )
    assert len(discovery.peers) == 1

    # A different service sharing the port is ignored.
    discovery._on_datagram(
        _beacon("other-app", "x", "C", 9100), ("192.168.1.10", 5555)
    )
    assert len(discovery.peers) == 1


def test_discovery_dedupes_repeated_beacons_and_reports_first_sight_only():
    seen: list[PeerInfo] = []
    discovery = PeerDiscovery("faxer", port=9100, on_peer=seen.append)
    beacon = _beacon("faxer", "other", "B", 9100)

    discovery._on_datagram(beacon, ("192.168.1.9", 5555))
    discovery._on_datagram(beacon, ("192.168.1.9", 5555))

    assert len(discovery.peers) == 1
    assert len(seen) == 1  # on_peer fires once, not on every beacon


async def test_discovery_starts_and_stops():
    discovery = PeerDiscovery(
        "faxer", port=9100, discovery_port=0, broadcast="127.0.0.1"
    )
    await discovery.start()
    assert discovery.is_running
    assert discovery.bound_port
    await discovery.stop()
    assert not discovery.is_running


async def test_discovery_receives_a_real_beacon_over_loopback():
    seen: list[PeerInfo] = []
    discovery = PeerDiscovery(
        "faxer", port=9100, discovery_port=0, on_peer=seen.append
    )
    await discovery.start()
    try:
        # Send a beacon from a separate, unbound UDP socket straight to the
        # listener, exercising the real OS receive path.
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sender.sendto(
                _beacon("faxer", "other", "FAX-B", 9100),
                ("127.0.0.1", discovery.bound_port),
            )
        finally:
            sender.close()
        await _wait_for(lambda: len(discovery.peers) == 1)
        peer = discovery.peers[0]  # capture before stop() clears the list
    finally:
        await discovery.stop()

    assert peer.host == "127.0.0.1"
    assert peer.port == 9100
    assert peer.name == "FAX-B"
    assert seen == [peer]
