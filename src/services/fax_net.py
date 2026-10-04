"""The fax protocol, layered on top of the generic :mod:`p2p` toolkit.

A real fax does not send a finished picture. It scans the page into horizontal
*lines* and transmits them one after another; the receiving machine prints each
line as it arrives. This module reproduces that: the sender streams the page's
grey rows as a sequence of small messages, and the receiver paints them onto a
canvas progressively, so the inbox fills from top to bottom.

Message kinds (the ``kind`` field of a :class:`p2p.Message`):

======================  ====================  =================================
kind                    data                  meta
======================  ====================  =================================
``fax.hello``           (empty)               station, sent_at, width, height,
                                              resolution
``fax.line``            raw grey row bytes    y (first row), rows (count)
``fax.end``             (empty)               checksum (CRC-32 of the page)
======================  ====================  =================================

The actual bytes on the wire are handled by ``p2p``; this module only decides
what the ``kind``\\ s mean and how to turn them back into an image.
"""

from __future__ import annotations

import asyncio
import inspect
import socket
import zlib
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any, Callable

from PIL import Image

from models.fax import ScanResult, render_transmit_page
from p2p import Message, send as p2p_send
from p2p.errors import P2PError
from p2p.server import Peer

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# The default port this app listens on. 9100 is the classic "raw printing" port,
# which feels right for a fax machine.
DEFAULT_FAX_PORT = 9100

# The service name broadcast by UDP discovery. Only instances announcing this
# exact string appear in the "Nearby faxes" list.
FAX_SERVICE = "faxer"

# How many image rows travel in one ``fax.line`` message. Smaller chunks make
# the receiver redraw more often (a smoother "printing" effect); larger chunks
# are more efficient.
DEFAULT_ROWS_PER_CHUNK = 4

# Artificial delay between chunks, in seconds. A real fax is slow, and without a
# little delay the whole page would appear instantly. Set to 0 for tests.
DEFAULT_LINE_DELAY = 0.01

# Don't redraw the inbox more often than this many seconds - the UI only needs
# to look smooth, and every redraw sends an update to the browser/desktop client.
MIN_REDRAW_INTERVAL = 0.03


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class FaxPage:
    """A complete fax page received from a peer."""

    station: str
    sent_at: str
    width: int
    height: int
    resolution: int
    image: Image.Image  # mode "L" (greyscale)
    sender: str = ""

    def to_png_bytes(self) -> bytes:
        """Encode the page as PNG bytes for display or saving."""
        buffer = BytesIO()
        self.image.save(buffer, format="PNG")
        return buffer.getvalue()


# A callback may be sync or async; the helpers below await only when needed.
AsyncOrSync = Callable[..., Any]


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable, else return it (see ``p2p.server``)."""
    if inspect.isawaitable(value):
        return await value
    return value


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


class FaxSender:
    """Transmits one scanned page to a peer, line by line."""

    def __init__(
        self,
        station: str = "",
        *,
        speed: float = DEFAULT_LINE_DELAY,
        rows_per_chunk: int = DEFAULT_ROWS_PER_CHUNK,
    ) -> None:
        self.station = station
        self.speed = speed
        self.rows_per_chunk = max(1, rows_per_chunk)

    async def send(
        self,
        host: str,
        port: int,
        scan: ScanResult,
        *,
        sent_at: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Render ``scan`` and stream it to ``host:port``.

        Raises :class:`p2p.errors.P2PError` (e.g. the peer refused the
        connection) so the caller can report a friendly message.
        """
        page = render_transmit_page(scan, self.station, sent_at)
        width, height = page.size
        # ``tobytes()`` on an "L" image gives one byte per pixel, row by row -
        # exactly the "scan lines" we want to stream.
        raw = page.tobytes()
        checksum = zlib.crc32(raw)

        async def frames():
            # 1. Announce the page so the receiver knows the canvas size.
            yield Message(
                kind="fax.hello",
                meta={
                    "station": self.station,
                    "sent_at": sent_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "width": width,
                    "height": height,
                    "resolution": scan.resolution,
                },
            )

            # 2. Slice the page into horizontal bands and send them in order.
            for y in range(0, height, self.rows_per_chunk):
                chunk = raw[y * width : (y + self.rows_per_chunk) * width]
                yield Message(
                    kind="fax.line",
                    data=chunk,
                    meta={"y": y, "rows": len(chunk) // width},
                )
                # Pacing: without this the receiver would get everything at once.
                if self.speed > 0:
                    await asyncio.sleep(self.speed)

            # 3. Tell the receiver the page is complete.
            yield Message(kind="fax.end", meta={"checksum": checksum})

        await p2p_send(host, port, frames(), timeout=timeout)


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------


class FaxReceiver:
    """Rebuilds incoming fax pages from ``fax.*`` messages.

    Wire it to a :class:`p2p.PeerServer` as its ``on_message`` handler::

        receiver = FaxReceiver(on_progress=..., on_complete=...)
        server = PeerServer(port=9100, on_message=receiver.handle)

    Callbacks (each may be ``def`` or ``async def``):

    * ``on_progress(peer, preview_png, rows_done, rows_total)`` - called while a
      page is arriving; ``preview_png`` is the partially filled page.
    * ``on_complete(peer, page)`` - a full :class:`FaxPage` is ready.
    * ``on_error(peer, exc)`` - a page failed (bad checksum, broken stream).
    """

    def __init__(
        self,
        *,
        on_progress: AsyncOrSync | None = None,
        on_complete: AsyncOrSync | None = None,
        on_error: AsyncOrSync | None = None,
    ) -> None:
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_error = on_error
        # One independent canvas per peer address, so two senders never mix.
        self._sessions: dict[Any, dict[str, Any]] = {}
        self._last_redraw_at: dict[Any, float] = {}

    async def handle(self, peer: Peer, message: Message) -> None:
        """Entry point handed to :class:`p2p.PeerServer` as ``on_message``."""
        kind = message.kind
        if kind == "fax.hello":
            self._start_session(peer, message.meta)
        elif kind == "fax.line":
            await self._append_line(peer, message)
        elif kind == "fax.end":
            await self._finish_session(peer, message.meta)
        # Unknown kinds are ignored on purpose: the protocol can grow.

    def forget(self, peer: Peer) -> None:
        """Discard any half-received page from ``peer`` (e.g. it disconnected)."""
        self._sessions.pop(peer.address, None)
        self._last_redraw_at.pop(peer.address, None)

    # -- session lifecycle ---------------------------------------------------
    def _start_session(self, peer: Peer, meta: dict[str, Any]) -> None:
        width = int(meta.get("width", 0))
        height = int(meta.get("height", 0))
        if width <= 0 or height <= 0:
            return
        self._sessions[peer.address] = {
            "width": width,
            "height": height,
            "station": str(meta.get("station", "")),
            "sent_at": str(meta.get("sent_at", "")),
            "resolution": int(meta.get("resolution", 0)),
            # ``bytearray`` is a mutable block of bytes we can paint into.
            "data": bytearray(width * height),
            "filled": 0,
            "checksum": None,
        }

    async def _append_line(self, peer: Peer, message: Message) -> None:
        session = self._sessions.get(peer.address)
        if session is None:
            return  # a line before its hello; ignore

        filled = session["filled"]
        end = filled + len(message.data)
        # Guard against a peer that sends more data than it announced.
        if end > len(session["data"]):
            end = len(session["data"])
            message.data = message.data[: end - filled]
        session["data"][filled:end] = message.data
        session["filled"] = end

        height = session["height"]
        width = session["width"]
        rows_done = end // width
        await self._emit_progress(peer, session, rows_done, height)

    async def _finish_session(self, peer: Peer, meta: dict[str, Any]) -> None:
        session = self._sessions.pop(peer.address, None)
        self._last_redraw_at.pop(peer.address, None)
        if session is None:
            return

        data = bytes(session["data"])
        expected = meta.get("checksum")
        if expected is not None and zlib.crc32(data) != int(expected):
            error = P2PError("received fax failed its checksum")
            if self.on_error is not None:
                await _maybe_await(self.on_error(peer, error))
            return

        image = Image.frombytes("L", (session["width"], session["height"]), data)
        page = FaxPage(
            station=session["station"],
            sent_at=session["sent_at"],
            width=session["width"],
            height=session["height"],
            resolution=session["resolution"],
            image=image,
            sender=peer.host_port,
        )
        if self.on_complete is not None:
            await _maybe_await(self.on_complete(peer, page))

    async def _emit_progress(
        self, peer: Peer, session: dict[str, Any], rows_done: int, rows_total: int
    ) -> None:
        """Redraw at most every ``MIN_REDRAW_INTERVAL`` seconds, always on finish."""
        if self.on_progress is None:
            return
        now = asyncio.get_running_loop().time()
        last = self._last_redraw_at.get(peer.address, 0.0)
        if rows_done < rows_total and (now - last) < MIN_REDRAW_INTERVAL:
            return
        self._last_redraw_at[peer.address] = now

        # Build a snapshot of the rows painted so far.
        width = session["width"]
        height = session["height"]
        preview = Image.frombytes("L", (width, height), bytes(session["data"]))
        # Encode to PNG here so the view only has to assign bytes.
        buffer = BytesIO()
        preview.save(buffer, format="PNG")
        await _maybe_await(
            self.on_progress(peer, buffer.getvalue(), rows_done, rows_total)
        )


# ---------------------------------------------------------------------------
# Small helpers shared by the controller
# ---------------------------------------------------------------------------


def parse_address(text: str | None) -> tuple[str, int]:
    """Turn "192.168.1.20:9100" (or a bare host) into ``(host, port)``.

    Raises :class:`ValueError` with a message we can show the user directly.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("enter a recipient like 192.168.1.20:9100")

    host, sep, port_text = text.rpartition(":")
    if not sep or not host:
        # No colon: assume the fax default port.
        host, port_text = text, str(DEFAULT_FAX_PORT)

    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"'{port_text}' is not a valid port number") from exc

    if not 0 < port < 65536:
        raise ValueError("port must be between 1 and 65535")
    return host, port


def compute_local_ip() -> str:
    """Best-effort local LAN address, shown so the other machine knows where to send.

    The trick: open a UDP socket "to" a public address. Nothing is sent, but the
    OS picks the interface it *would* use, and we read that interface's IP. If
    that fails we fall back to the hostname lookup, then to loopback.
    """
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
        finally:
            probe.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def default_station_id() -> str:
    """A friendly default "fax number" derived from this machine's name."""
    try:
        name = socket.gethostname()
    except OSError:
        name = "local"
    return f"FAX-{name.upper()}"[:24]
