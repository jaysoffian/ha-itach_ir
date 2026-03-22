"""Global Caché iTach TCP client for sending IR commands."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 4998
CONNECT_TIMEOUT = 10.0
RESPONSE_TIMEOUT = 10.0
IDLE_TIMEOUT = 60.0

# iTach API Specification v1.5, Section 6
_ERR_CODES: dict[int, str] = {
    1: "Invalid command. Command not found",
    2: "Invalid module address (does not exist)",
    3: "Invalid connector address (does not exist)",
    4: "Invalid ID value",
    5: "Invalid frequency value",
    6: "Invalid repeat value",
    7: "Invalid offset value",
    8: "Invalid pulse count",
    9: "Invalid pulse data",
    10: "Uneven amount of <on|off> statements",
    11: "No carriage return found",
    12: "Repeat count exceeded",
    13: "IR command sent to input connector",
    14: "Blaster command sent to non-blaster connector",
    15: "No carriage return before buffer full",
    16: "No carriage return",
    17: "Bad command syntax",
    18: "Sensor command sent to non-input connector",
    19: "Repeated IR transmission failure",
    20: "Above designated IR <on|off> pair limit",
    21: "Symbol odd boundary",
    22: "Undefined symbol",
    23: "Unknown option",
    24: "Invalid baud rate setting",
    25: "Invalid flow control setting",
    26: "Invalid parity setting",
    27: "Settings are locked",
}


def _format_error(resp: str) -> str:
    """Parse an iTach error response into a human-readable message.

    Error format: ERR_<connaddr>,<code>  (e.g. ERR_1:1,006)
    Also handles: busyIR,<connaddr>,<ID>
                  unknowncommand,<error_code>
    """
    if resp.startswith("busyIR"):
        return f"{resp}: IR port is busy (already transmitting)"

    if resp.startswith("unknowncommand"):
        return f"{resp}: command not recognized by device"

    if resp.startswith("ERR"):
        # ERR_1:1,006 → code 6
        try:
            code_str = resp.rsplit(",", 1)[1]
            code = int(code_str)
            desc = _ERR_CODES.get(code, "unknown error")
            return f"{resp}: {desc}"
        except (IndexError, ValueError):
            pass

    return resp


class ITachClient:
    """Low-level TCP client for a Global Caché iTach device.

    Maintains a persistent TCP connection that is opened lazily on the first
    command and closed after ``idle_timeout`` seconds of inactivity.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        name: str = "",
        idle_timeout: float = IDLE_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._name = name or host
        self._idle_timeout = idle_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._idle_handle: asyncio.TimerHandle | None = None

    async def _ensure_connected(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Return an open connection, connecting if necessary."""
        if self._writer is not None and not self._writer.is_closing():
            return self._reader, self._writer  # type: ignore[return-value]

        self._reader = None
        self._writer = None

        _LOGGER.debug(
            "[iTach] %s: connecting to %s:%s", self._name, self._host, self._port
        )
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=CONNECT_TIMEOUT,
            )
        except Exception as e:
            _LOGGER.error(
                "[iTach] %s: failed to connect to %s:%s: %s: %s",
                self._name,
                self._host,
                self._port,
                type(e).__name__,
                e,
            )
            raise

        self._reader = reader
        self._writer = writer
        return reader, writer

    def _reset_idle_timer(self) -> None:
        """(Re)start the idle disconnect timer."""
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(self._idle_timeout, self._idle_disconnect)

    def _idle_disconnect(self) -> None:
        """Close the connection after idle timeout."""
        self._idle_handle = None
        if self._writer is not None and not self._writer.is_closing():
            _LOGGER.debug("[iTach] %s: closing idle connection", self._name)
            self._writer.close()
        self._reader = None
        self._writer = None

    async def _close(self) -> None:
        """Close the connection immediately."""
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None
        if self._writer is not None and not self._writer.is_closing():
            self._writer.close()
            await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    async def _send_and_receive(self, command: str) -> str:
        """Send a command on the persistent connection and return the response.

        On connection failure, drops the connection so the next call reconnects.
        """
        try:
            reader, writer = await self._ensure_connected()
        except Exception:
            return ""

        payload = f"{command}\r".encode("ascii")
        _LOGGER.debug("[iTach] %s >>> %r", self._name, payload)

        try:
            writer.write(payload)
            await writer.drain()
            resp = await asyncio.wait_for(
                reader.readuntil(b"\r"), timeout=RESPONSE_TIMEOUT
            )
        except Exception as e:
            _LOGGER.error(
                "[iTach] %s: communication error: %s: %s",
                self._name,
                type(e).__name__,
                e,
            )
            await self._close()
            return ""

        self._reset_idle_timer()
        decoded = resp.decode("ascii").rstrip("\r")
        _LOGGER.debug("[iTach] %s <<< %r", self._name, decoded)
        return decoded

    async def send(self, command: str) -> str:
        """Send a command and return the response line."""
        return await self._send_and_receive(command)

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

        Uses a dedicated connection (not the pooled one) since learning
        is a long-lived streaming operation.
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
        Send a sendir command over the persistent connection.

        data: "<connaddr>,<ID>,<freq>,<repeat>,<offset>,<on>,<off>,..."

        Sends:    sendir,<data>\r
        Expects:  completeir,<connaddr>,<ID>\r
        """
        fields = data.split(",", 2)
        if len(fields) < 2:
            _LOGGER.error("[iTach] %s: malformed sendir data %r", self._name, data)
            return

        connector_address, command_id = fields[0], fields[1]
        expected = f"completeir,{connector_address},{command_id}"

        resp = await self._send_and_receive(f"sendir,{data}")
        if not resp:
            return  # error already logged by _send_and_receive

        if resp != expected:
            _LOGGER.error(
                "[iTach] %s: %s",
                self._name,
                _format_error(resp),
            )
