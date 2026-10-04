# p2p - a tiny peer-to-peer messaging toolkit

A small, dependency-free (standard-library-only) way for two Python programs to
talk to each other over TCP. The `faxer` app uses it to send faxes over a LAN,
but nothing in here knows about faxes, images, Flet or Pillow - so you can copy
this folder into **any** Python project and `import p2p`.

## Install by copying

Copy the `p2p/` folder so that it sits on your project's import path, then:

```python
from p2p import Message, PeerServer, send
```

That's it. No pip, no third-party packages.

## Concepts

A **message** is a `kind` (a short name), optional JSON `meta`, and raw `data`
bytes:

```python
from p2p import Message

Message(kind="chat", data=b"hello", meta={"lang": "en"})
```

Messages are wrapped in a small framed format on the wire (a `P2P1` magic,
a length-prefixed JSON header, then the payload). `message.py` documents it in
detail. You never have to build frames yourself.

## Receiving

```python
import asyncio
from p2p import PeerServer

async def handle_message(peer, message):
    # `peer.host_port` is "host:port"; `peer.send(...)` replies to this client.
    print("from", peer.host_port, ":", message.kind, message.data)
    await peer.send(Message("ack", b"ok"))

async def main():
    server = PeerServer(host="0.0.0.0", port=9100, on_message=handle_message)
    port = await server.start()     # actually listening; returns the real port
    print("listening on", port)
    await asyncio.Event().wait()    # run forever

asyncio.run(main())
```

`port=0` asks the OS for any free port - great for tests, since `start()`
returns the number that was chosen.

Callbacks may be plain functions **or** `async def`; the server awaits them
either way. Available callbacks: `on_connect`, `on_message`, `on_disconnect`,
`on_error`.

## Sending

```python
from p2p import Message, send

# One message:
await send("192.168.1.20", 9100, Message("hello", b"hi"))

# Several messages over one connection:
await send("192.168.1.20", 9100, [Message("a", b"1"), Message("b", b"2")])

# Stream slowly (an async generator) - messages arrive gradually:
async def frames():
    for i in range(100):
        yield Message("line", str(i).encode())
        await asyncio.sleep(0.02)

await send("192.168.1.20", 9100, frames())
```

Every call opens a fresh connection, writes the messages in order, then closes.

## Finding peers on the LAN (discovery)

`PeerDiscovery` lets peers find each other without typing IP addresses. Each
instance broadcasts a tiny JSON *beacon* to `255.255.255.255` every couple of
seconds and listens for everyone else's:

```python
from p2p import PeerDiscovery, PeerInfo

def on_peer(info: PeerInfo):
    print("found", info.name, "at", info.address)   # e.g. FAX-B at 192.168.1.9:9100

discovery = PeerDiscovery(
    service="myapp",       # only beacons with this string are accepted
    port=9100,             # the TCP port peers should connect to
    name="MY-MACHINE",
    on_peer=on_peer,
)
await discovery.start()
...
print([p.address for p in discovery.peers])   # current peers
await discovery.stop()
```

- `on_peer(info)` fires when a peer is first seen; `on_lost(info)` when it has
  been silent for `ttl` seconds (default 6). The list self-heals as machines
  come and go.
- Both callbacks may be `def` or `async def`.
- The sender's IP comes from the datagram itself, so it cannot be spoofed by
  the payload.
- Discovery uses UDP port `9101` by default (separate from the TCP data port)
  and needs `SO_BROADCAST`. Two instances on **the same machine** may not see
  each other because they compete for that UDP port - it is designed for
  one-instance-per-machine on a LAN.

## Errors

All failures derive from `p2p.errors.P2PError`:

| exception            | meaning                                        |
| -------------------- | ---------------------------------------------- |
| `P2PConnectionError` | could not connect, dropped, or timed out       |
| `P2PProtocolError`   | the other side is not speaking our frame format |
| `P2PPayloadTooLarge` | a frame exceeded the safety limit              |

```python
from p2p.errors import P2PError
try:
    await send(host, port, messages)
except P2PError as exc:
    print("network problem:", exc)
```

## Safety limits

- Header ≤ 64 KB, payload ≤ 20 MB per message (configurable on the server).
- Connect and send timeouts (30 s by default; pass `timeout=`/`connect_timeout=`).

These stop a buggy or hostile peer from exhausting memory or hanging your app.
