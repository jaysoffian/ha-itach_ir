"""Global Caché iTach TCP client for sending IR commands."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 4998
CONNECT_TIMEOUT = 10.0
RESPONSE_TIMEOUT = 10.0


class ITachClient:
    """Low-level TCP client for a Global Caché iTach device."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, name: str = "") -> None:
        self._host = host
        self._port = port
        self._name = name or host

    async def send(self, command: str) -> str:
        """Send a command and return the response line."""
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=CONNECT_TIMEOUT,
            )
            payload = f"{command}\r".encode("ascii")
            _LOGGER.debug("[iTach] %s >>> %r", self._name, payload)
            writer.write(payload)
            await writer.drain()
            resp = await asyncio.wait_for(
                reader.readuntil(b"\r"), timeout=RESPONSE_TIMEOUT
            )
            _LOGGER.debug("[iTach] %s <<< %r", self._name, resp)
            return resp.decode("ascii").rstrip("\r")
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()

    async def getdevices(self) -> str:
        """Query connected modules."""
        return await self.send("getdevices")

    async def getversion(self) -> str:
        """Query firmware version."""
        return await self.send("getversion")

    async def learn(self) -> AsyncIterator[str]:
        """Start IR learning mode and yield received IR strings.

        Sends get_IRL, then yields each response line until the caller
        breaks out of the iterator (which sends stop_IRL).
        """
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=CONNECT_TIMEOUT,
            )
            writer.write(b"get_IRL\r")
            await writer.drain()
            while True:
                line = await reader.readuntil(b"\r")
                yield line.decode("ascii").rstrip("\r")
        except asyncio.CancelledError:
            pass
        finally:
            if writer is not None:
                # Best-effort stop before closing
                try:
                    writer.write(b"stop_IRL\r")
                    await writer.drain()
                except OSError:
                    pass
                writer.close()
                await writer.wait_closed()

    async def stop_learn(self) -> str:
        """Send stop_IRL on a fresh connection (useful if learn is on another)."""
        return await self.send("stop_IRL")

    async def sendir(self, data: str) -> None:
        """
        Open a TCP connection to the iTach and send a sendir command.

        data: "<connaddr>,<ID>,<freq>,<repeat>,<offset>,<on>,<off>,..."

        Sends:    sendir,<data>\r
        Expects:  completeir,<connaddr>,<ID>\r
        """
        fields = data.split(",", 2)
        if len(fields) < 2:
            _LOGGER.error("[iTach] %s: malformed sendir data %r", self._name, data)
            return

        connector_address, command_id = fields[0], fields[1]
        sendir = f"sendir,{data}\r".encode("ascii")
        completeir = f"completeir,{connector_address},{command_id}\r".encode("ascii")

        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=CONNECT_TIMEOUT,
            )

            _LOGGER.debug("[iTach] %s >>> %r", self._name, sendir)
            writer.write(sendir)
            await writer.drain()

            resp = await asyncio.wait_for(
                reader.readuntil(b"\r"),
                timeout=RESPONSE_TIMEOUT,
            )
            _LOGGER.debug("[iTach] %s <<< %r", self._name, resp)
            if resp != completeir:
                _LOGGER.error(
                    "[iTach] %s: unexpected response %r (expected %r)",
                    self._name,
                    resp,
                    completeir,
                )

        except Exception as e:
            _LOGGER.error(
                "[iTach] %s: sendir failed: %s: %s",
                self._name,
                type(e).__name__,
                e,
            )

        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
