#!/usr/bin/env -S uv run --group cli python
"""
CLI for interacting with a Global Caché iTach IP2IR.

https://www.globalcache.com/files/docs/API-iTach.pdf
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import socket
import struct
import sys
import time
from pathlib import Path

import typer

# Allow importing client.py from custom_components/itach_ir/
sys.path.insert(0, str(Path(__file__).parent / "custom_components" / "itach_ir"))

from client import DEFAULT_PORT, ITachClient

cli = typer.Typer()

_BEACON_RE = re.compile(
    r"""
    AMXB
    <-UUID=GlobalCache_(?P<UUID>[^>]+)>
    <-SDKClass=Utility>
    <-Make=GlobalCache>
    <-Model=(?P<Model>[^>]+)>
    <-Revision=(?P<Revision>[^>]+)>
    <-Pkg_Level=(?P<Pkg_Level>[^>]+)>
    <-Config-URL=http://(?P<IP>[^>]+)>
    <-PCB_PN=(?P<PN>[^>]+)>
    <-Status=(?P<Status>[^>]+)>\r
    """,
    re.VERBOSE,
)


def _client() -> ITachClient:
    host = os.environ.get("ITACH_HOST")
    if not host:
        typer.echo("ITACH_HOST not set. Run './cli.py discover' to find your device.")
        raise SystemExit(1)
    port = int(os.environ.get("ITACH_PORT", str(DEFAULT_PORT)))
    return ITachClient(host, port)


@cli.command()
def discover(timeout: float = 10) -> None:
    """Listen for iTach AMX beacon broadcasts."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 9131))

    group = socket.inet_aton("239.255.250.250")
    mreq = struct.pack("4sL", group, socket.INADDR_ANY)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    typer.echo(f"Listening for iTach beacons ({timeout:.0f}s)...", nl=False)
    deadline = time.monotonic() + timeout
    seen: dict[str, dict[str, str]] = {}
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            s.settimeout(min(remaining, 1.0))
            try:
                data = s.recv(1024)
            except TimeoutError:
                typer.echo(".", nl=False)
                continue
            match = _BEACON_RE.match(data.decode("ascii"))
            if match:
                info = match.groupdict()
                if info["UUID"] not in seen:
                    seen[info["UUID"]] = info
                    typer.echo("!", nl=False)
    finally:
        s.close()

    typer.echo()
    if not seen:
        typer.echo("No iTach discovered")
        raise SystemExit(1)
    for dev in seen.values():
        typer.echo(json.dumps(dev, indent=2))
        typer.echo(f"\nexport ITACH_HOST={dev['IP']}")


@cli.command()
def sendir(ir_string: str, ir_port: int = 1) -> None:
    """Send an IR command string."""
    connector_address = f"1:{ir_port}"
    command_id = random.randint(0, 65535)
    data = f"{connector_address},{command_id},{ir_string}"
    asyncio.run(_client().sendir(data))


@cli.command()
def getdevices() -> None:
    """Query connected modules."""
    typer.echo(asyncio.run(_client().getdevices()))


@cli.command()
def getversion() -> None:
    """Query firmware version."""
    typer.echo(asyncio.run(_client().getversion()))


@cli.command()
def learn() -> None:
    """Enter IR learning mode. Press Ctrl-C to stop."""

    async def _learn() -> None:
        client = _client()
        try:
            async for line in client.learn():
                typer.echo(line)
        except KeyboardInterrupt:
            pass

    asyncio.run(_learn())


@cli.command()
def stop_learn() -> None:
    """Send stop_IRL (e.g. if learn was left running)."""
    typer.echo(asyncio.run(_client().stop_learn()))


if __name__ == "__main__":
    cli()
