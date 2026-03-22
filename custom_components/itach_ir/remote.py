"""
Global Caché iTach IR remote platform.

Accepts GC sendir format data strings natively — no Pronto conversion.

Example configuration.yaml:

    remote:
      - platform: itach_ir
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

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.components.remote import PLATFORM_SCHEMA, RemoteEntity
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .client import DEFAULT_PORT, ITachClient  # ty: ignore[unresolved-import]

_LOGGER = logging.getLogger(__name__)

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

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(  # type: ignore[reportConstantRedefinition]
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_NAME): cv.string,
        vol.Required(CONF_DEVICES): vol.All(cv.ensure_list, [DEVICE_SCHEMA]),
    }
)

DATA_KEY = "itach_ir_locks"


@dataclass
class IRStep:
    """A single step in a command sequence."""

    data: str
    send_count: int = 1
    interval: float = 0.1
    pause: float = 0.0


def _parse_command_data(data: str | list[dict[str, Any]]) -> list[IRStep]:
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
            send_count=step["send_count"],
            interval=step["interval"],
            pause=step["pause"],
        )
        for step in data
    ]


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up iTach remote entities from configuration."""
    host = config[CONF_HOST]
    port = config[CONF_PORT]
    itach_name = config.get(CONF_NAME, host)

    # One lock per host, shared across all device entities on the same host.
    # Prevents concurrent sends from stomping on each other.
    locks: dict[str, asyncio.Lock] = hass.data.setdefault(DATA_KEY, {})
    if host not in locks:
        locks[host] = asyncio.Lock()

    entities = [
        ITachRemote(
            hass=hass,
            name=device["name"],
            host=host,
            port=port,
            itach_name=itach_name,
            commands={
                cmd["name"]: _parse_command_data(cmd["data"])
                for cmd in device[CONF_COMMANDS]
            },
        )
        for device in config[CONF_DEVICES]
    ]

    async_add_entities(entities)


class ITachRemote(RemoteEntity, RestoreEntity):
    """Represents a collection of IR commands sent via a Global Caché iTach."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        host: str,
        port: int,
        itach_name: str,
        commands: dict[str, list[IRStep]],
    ) -> None:
        self.hass = hass
        self._attr_name = name
        self._host = host
        self._commands = commands
        self._client = ITachClient(host, port, name)
        self._attr_unique_id = f"itach_ir_{itach_name}_{name}".lower().replace(" ", "_")
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Restore last known on/off state."""
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == STATE_ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send the 'turn_on' command if configured, otherwise no-op."""
        self._attr_is_on = True
        self.async_write_ha_state()
        if "turn_on" in self._commands:
            await self._send("turn_on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send the 'turn_off' command if configured, otherwise no-op."""
        self._attr_is_on = False
        self.async_write_ha_state()
        if "turn_off" in self._commands:
            await self._send("turn_off")

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send one or more named IR commands."""
        num_repeats: int = kwargs.get("num_repeats", 1)
        delay_secs: float = kwargs.get("delay_secs", 0.5)

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

        lock: asyncio.Lock = self.hass.data[DATA_KEY][self._host]
        async with lock:
            for step in steps:
                # Optional pause before this step (e.g. waiting for device
                # to be ready after a previous command)
                if step.pause:
                    await asyncio.sleep(step.pause)

                data = "".join(step.data.split())  # strip whitespace from YAML folding

                for i in range(step.send_count):
                    await self._client.sendir(data)
                    if step.send_count > 1 and i < step.send_count - 1:
                        await asyncio.sleep(step.interval)
