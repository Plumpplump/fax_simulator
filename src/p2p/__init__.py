"""``p2p`` - a tiny, reusable peer-to-peer messaging toolkit.

This package lets two Python programs (on the same LAN, or even the same
machine) exchange labelled messages over TCP. It has **no** dependency on Flet,
Pillow, or the fax simulator - the only imports are the Python standard library.
That is deliberate: copy the ``src/p2p`` folder into any other project and
``import p2p`` works there unchanged.

Public API
----------
:class:`Message`
    A ``kind`` string, optional ``meta`` dict, and raw ``data`` bytes.
:class:`PeerServer`
    Listens for connections and calls ``on_message(peer, message)``.
:func:`send`
    Dials a peer and writes one message or a whole stream of them.

Quick start
-----------
Receiver::

    from p2p import PeerServer

    async def handle(peer, message):
        print("from", peer.host_port, "->", message.kind, message.data)

    server = PeerServer(port=9100, on_message=handle)
    await server.start()

Sender::

    from p2p import Message, send

    await send("192.168.1.20", 9100, Message("hello", b"hi"))
"""

from .client import send
from .discovery import (
    DEFAULT_DISCOVERY_PORT,
    DISCOVERY_MAGIC,
    PeerDiscovery,
    PeerInfo,
)
from .errors import (
    P2PConnectionError,
    P2PError,
    P2PPayloadTooLarge,
    P2PProtocolError,
)
from .message import DEFAULT_MAX_PAYLOAD_BYTES, MAGIC, Message, encode, read_message
from .server import Peer, PeerServer

__all__ = [
    # core
    "Message",
    "PeerServer",
    "Peer",
    "send",
    "encode",
    "read_message",
    # discovery
    "PeerDiscovery",
    "PeerInfo",
    # constants
    "MAGIC",
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "DEFAULT_DISCOVERY_PORT",
    "DISCOVERY_MAGIC",
    # exceptions
    "P2PError",
    "P2PConnectionError",
    "P2PProtocolError",
    "P2PPayloadTooLarge",
]
