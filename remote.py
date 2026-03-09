"""
Global Caché iTach IR remote platform.

Accepts GC sendir format data strings natively — no Pronto conversion.

Example configuration.yaml:

    remote:
      - platform: itach
        host: 192.168.1.197
        devices:
          - name: JVC DLA
            commands:
              - name: power_on
                data: >-
                  1:1,0,38000,1,37,319,160,20,60,...

          - name: Lumagen
            commands:
              - name: power_on
                data:
                  - data: "1:1,0,38000,..."
                    send_count: 1
                    interval: 0.1
              - name: power_off
                data:
                  - data: "1:1,1,38000,..."
                    send_count: 1

          - name: Denon
            commands:
              - name: power_on
                # Two distinct IR bursts with a pause between them
                data:
                  - data: "1:1,0,38000,..."
                    send_count: 2
                    interval: 0.1
                  - data: "1:1,1,38000,..."
                    pause: 0.5
                    send_count: 1

The data field for a command is one of:
  - A plain string (send once)
  - A list of step objects, each with:
      data:       required  GC sendir string
      send_count: optional  number of times to send this step (default 1)
      interval:   optional  seconds between repeats within this step (default 0.1)
      pause:      optional  seconds to wait BEFORE this step (default 0)

To send a command from an automation or script:
  service: remote.send_command
  target:
    entity_id: remote.jvc_dla
  data:
    command: power_on
"""

import asyncio
import logging
from dataclasses import dataclass

import voluptuous as vol

from homeassistant.components.remote import PLATFORM_SCHEMA, RemoteEntity
from homeassistant.const import CONF_HOST, CONF_PORT
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 4998
CONNECT_TIMEOUT = 10.0
RESPONSE_TIMEOUT = 10.0

CONF_DEVICES = "devices"
CONF_COMMANDS = "commands"

# Schema for a single step in a multi-step command sequence
STEP_SCHEMA = vol.Schema(
    {
        vol.Required("data"): cv.string,
        vol.Optional("send_count", default=1): vol.All(int, vol.Range(min=1)),
        vol.Optional("interval", default=0.1): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
        vol.Optional("pause", default=0): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)

# A command's data is either a plain string or a list of steps
DATA_SCHEMA = vol.Any(
    cv.string,
    vol.All(cv.ensure_list, [STEP_SCHEMA]),
)

COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("data"): DATA_SCHEMA,
    }
)

DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional(CONF_COMMANDS, default=[]): vol.All(
            cv.ensure_list, [COMMAND_SCHEMA]
        ),
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Required(CONF_DEVICES): vol.All(cv.ensure_list, [DEVICE_SCHEMA]),
    }
)

# One lock per host, shared across all device entities on the same host.
# Prevents concurrent sends from stomping on each other, matching the
# mutex-per-device behavior in the JS implementation.
_HOST_LOCKS: dict[str, asyncio.Lock] = {}


def _get_lock(host: str) -> asyncio.Lock:
    if host not in _HOST_LOCKS:
        _HOST_LOCKS[host] = asyncio.Lock()
    return _HOST_LOCKS[host]


@dataclass
class IRStep:
    """A single step in a command sequence."""

    data: str
    send_count: int = 1
    interval: float = 0.1
    pause: float = 0.0


def _parse_command_data(data) -> list[IRStep]:
    """
    Normalize command data to a list of IRStep regardless of input format.

    Accepts:
      - A plain string  → one step, send_count=1
      - A list of dicts → one step per dict
    """
    if isinstance(data, str):
        return [IRStep(data=data)]
    return [
        IRStep(
            data=step["data"],
            send_count=step.get("send_count", 1),
            interval=step.get("interval", 0.1),
            pause=step.get("pause", 0),
        )
        for step in data
    ]


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up iTach remote entities from configuration."""
    host = config[CONF_HOST]
    port = config[CONF_PORT]

    entities = [
        ITachRemote(
            name=device["name"],
            host=host,
            port=port,
            commands={
                cmd["name"]: _parse_command_data(cmd["data"])
                for cmd in device[CONF_COMMANDS]
            },
        )
        for device in config[CONF_DEVICES]
    ]

    async_add_entities(entities)


class ITachRemote(RemoteEntity):
    """Represents a collection of IR commands sent via a Global Caché iTach."""

    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        commands: dict[str, list[IRStep]],
    ) -> None:
        self._attr_name = name
        self._host = host
        self._port = port
        self._commands = commands
        # Remote entities need an is_on state. Since iTach is stateless
        # (we have no way to query device state), we always report True
        # so the entity appears active and ready to accept commands.
        self._attr_is_on = True

    async def async_turn_on(self, **kwargs):
        """Required by RemoteEntity but not meaningful for a stateless IR blaster."""

    async def async_turn_off(self, **kwargs):
        """Required by RemoteEntity but not meaningful for a stateless IR blaster."""

    async def async_send_command(self, command: list[str], **kwargs) -> None:
        """
        Send one or more named IR commands.

        Supports the standard HA remote service call fields:
          num_repeats: number of times to repeat the entire command sequence
          delay_secs:  seconds to wait between top-level repeats
        """
        num_repeats = kwargs.get("num_repeats", 1)
        delay_secs = kwargs.get("delay_secs", 0.5)

        for i in range(num_repeats):
            if i > 0 and delay_secs:
                await asyncio.sleep(delay_secs)
            for cmd in command:
                await self._send(cmd)

    async def _send(self, command: str) -> None:
        """Look up and execute a named command's step sequence."""
        steps = self._commands.get(command)
        if steps is None:
            _LOGGER.error(
                "[iTach] %s: unknown command %r (known: %s)",
                self._attr_name,
                command,
                ", ".join(self._commands),
            )
            return

        async with _get_lock(self._host):
            for step in steps:
                # Optional pause before this step (e.g. waiting for device
                # to be ready after a previous command)
                if step.pause:
                    await asyncio.sleep(step.pause)

                data = " ".join(step.data.split())  # normalize whitespace

                for i in range(step.send_count):
                    await self._sendir(data)
                    if step.send_count > 1 and i < step.send_count - 1:
                        await asyncio.sleep(step.interval)

    async def _sendir(self, data: str) -> None:
        """
        Open a TCP connection to the iTach and send a sendir command.

        Sends:    sendir,<data>\r
        Expects:  completeir,<connaddr>,<ID>\r

        Uses latin-1 encoding (not ascii) to safely handle any high bytes
        that may appear in learned IR codes.

        Mirrors the JS implementation: reads exactly as many bytes as the
        expected completeir response rather than using readline(), and does
        a hard close (abort) rather than a graceful shutdown.
        """
        # connaddr and command ID are the first two comma-separated fields,
        # e.g. "1:1" and "0" from "1:1,0,38000,..."
        fields = data.split(",", 2)
        if len(fields) < 2:
            _LOGGER.error("[iTach] %s: malformed data %r", self._attr_name, data)
            return

        connector_address, command_id = fields[0], fields[1]
        sendir = f"sendir,{data}\r".encode("latin-1")
        completeir = f"completeir,{connector_address},{command_id}\r".encode("latin-1")

        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=CONNECT_TIMEOUT,
            )

            _LOGGER.debug("[iTach] %s >>> %r", self._attr_name, sendir)
            writer.write(sendir)
            await writer.drain()

            try:
                resp = await asyncio.wait_for(
                    reader.readexactly(len(completeir)),
                    timeout=RESPONSE_TIMEOUT,
                )
                _LOGGER.debug("[iTach] %s <<< %r", self._attr_name, resp)
                if resp != completeir:
                    _LOGGER.error(
                        "[iTach] %s: unexpected response %r (expected %r)",
                        self._attr_name,
                        resp,
                        completeir,
                    )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "[iTach] %s: timed out waiting for completeir",
                    self._attr_name,
                )
            except asyncio.IncompleteReadError as e:
                _LOGGER.error(
                    "[iTach] %s: connection closed before completeir: %r",
                    self._attr_name,
                    e.partial,
                )

        except Exception as e:
            _LOGGER.error("[iTach] %s: send failed: %s", self._attr_name, e)

        finally:
            # Hard close matching JS sock.destroy()
            if writer is not None:
                writer.transport.abort()
