"""Tests for the fax protocol built on top of :mod:`p2p`.

The end-to-end test runs a real loopback transmission (``speed=0`` so it is
instant) and checks that the receiver rebuilds the page line by line.
"""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from PIL import Image

from models.fax import FAX_HEADER_HEIGHT, WORK_SIZE, FaxState, render_transmit_page
from p2p import Message, PeerServer
from p2p.server import Peer
from services.fax_net import FaxReceiver, FaxSender, parse_address


def make_image(width: int = 64, height: int = 64) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (30, 90, 150)).save(buffer, format="PNG")
    return buffer.getvalue()


def make_scan(resolution: int = 8):
    state = FaxState()
    state.load(make_image())
    state.resolution = resolution
    return state.scan()


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before the timeout")


def test_render_transmit_page_is_grey_with_a_header_band():
    page = render_transmit_page(make_scan(), "555-0100", "2026-10-04 12:00")
    assert page.mode == "L"
    assert page.size == (WORK_SIZE, WORK_SIZE + FAX_HEADER_HEIGHT)


async def test_loopback_fax_is_received_line_by_line():
    completed: dict = {}
    progress: list[tuple[int, int]] = []

    def on_progress(peer, preview_png, done, total):
        progress.append((done, total))
        # Every preview is a valid PNG snapshot of the partial page.
        assert preview_png.startswith(b"\x89PNG")

    async def on_complete(peer, page):
        completed["page"] = page

    receiver = FaxReceiver(on_progress=on_progress, on_complete=on_complete)
    server = PeerServer(port=0, on_message=receiver.handle)
    port = await server.start()

    sender = FaxSender(station="TEST-STATION", speed=0.0)
    try:
        await sender.send("127.0.0.1", port, make_scan())
        await _wait_for(lambda: "page" in completed)
    finally:
        await server.stop()

    page = completed["page"]
    assert page.station == "TEST-STATION"
    assert page.resolution == 8
    assert page.image.size == (WORK_SIZE, WORK_SIZE + FAX_HEADER_HEIGHT)
    # The last progress report is always a complete page.
    assert progress and progress[-1][0] == progress[-1][1]


async def test_receiver_reports_checksum_errors():
    errors: list[Exception] = []

    def on_error(peer, exc):
        errors.append(exc)

    receiver = FaxReceiver(on_error=on_error)
    # Feed a hello + a line + a bogus end checksum directly (no socket needed).
    peer = Peer.__new__(Peer)  # bypass __init__; we only need an identity
    peer.address = ("127.0.0.1", 12345)

    await receiver.handle(peer, Message("fax.hello", meta={"width": 2, "height": 2}))
    await receiver.handle(peer, Message("fax.line", data=b"\x01\x02\x03\x04"))
    await receiver.handle(peer, Message("fax.end", meta={"checksum": 999}))

    assert errors and "checksum" in str(errors[0])


def test_parse_address_forms():
    assert parse_address("192.168.1.20:9100") == ("192.168.1.20", 9100)
    # A bare host falls back to the default fax port.
    assert parse_address("faxbox.local") == ("faxbox.local", 9100)


def test_parse_address_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_address("")
    with pytest.raises(ValueError):
        parse_address("host:not-a-port")
    with pytest.raises(ValueError):
        parse_address("host:70000")
