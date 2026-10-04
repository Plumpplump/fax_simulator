"""The dialling half of the toolkit: :func:`send`.

Open a connection to a peer, write one or more framed messages, close. That is
the whole job. Because ``messages`` may be a *generator*, this supports
streaming: you can yield a message, ``await asyncio.sleep(...)``, then yield the
next one, and the receiver sees them arrive gradually.

Example - send two messages::

    from p2p import Message, send
    await send("192.168.1.20", 9100, [Message("hello", b"1"), Message("bye", b"2")])

Example - stream a big transfer slowly::

    async def frames():
        for chunk in chunks:
            yield Message("chunk", chunk)
            await asyncio.sleep(0.01)
    await send("192.168.1.20", 9100, frames())
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterable, Iterable

from .errors import P2PConnectionError
from .message import Message, encode

DEFAULT_TIMEOUT = 30.0


async def send(
    host: str,
    port: int,
    messages: Message | Iterable[Message] | AsyncIterable[Message],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    connect_timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Send ``messages`` to ``host:port`` over one connection.

    ``messages`` accepts a single :class:`Message`, a normal iterable (list,
    tuple, generator) or an *async* iterable (``async def`` generator). Every
    message is encoded with :func:`p2p.message.encode` and flushed before the
    next one, so ordering is preserved and the peer sees a smooth stream.
    """
    # Allow the convenient ``send(host, port, one_message)`` form.
    if isinstance(messages, Message):
        messages = [messages]

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), connect_timeout
        )
    except (OSError, asyncio.TimeoutError) as exc:
        # Normalise every "could not connect" reason into one clear exception.
        raise P2PConnectionError(f"could not connect to {host}:{port}: {exc}") from exc

    async def pump() -> None:
        # ``hasattr(..., "__aiter__")`` distinguishes an async generator from a
        # plain list without importing typing helpers.
        if hasattr(messages, "__aiter__"):
            async for message in messages:  # type: ignore[union-attr]
                writer.write(encode(message))
                await writer.drain()
        else:
            for message in messages:  # type: ignore[union-attr]
                writer.write(encode(message))
                await writer.drain()

    try:
        await asyncio.wait_for(pump(), timeout)
    except asyncio.TimeoutError as exc:
        raise P2PConnectionError(f"sending to {host}:{port} timed out") from exc
    except OSError as exc:
        raise P2PConnectionError(f"connection to {host}:{port} broke: {exc}") from exc
    finally:
        # The connection is one-shot: we close it once the messages are out.
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            pass


__all__ = ["send", "DEFAULT_TIMEOUT"]
