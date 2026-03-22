# Global Caché iTach IR Integration for Home Assistant

This [integration](https://www.home-assistant.io/getting-started/concepts-terminology/#integrations) connects your [Global Caché](https://www.globalcache.com/products.html) iTach [IP2IR](https://www.google.com/search?q=Global+Cache+iTach+IP2IR) to your [Home Assistant](https://www.home-assistant.io) installation.

This is an alternative to HA's [built-in itach](https://www.home-assistant.io/integrations/itach/) integration.

Unlike the built-in integration, this integration accepts the native **sendir** data format,
eliminating the lossy Pronto hex conversion that the built-in integration requires.

If you have learned IR codes, or codes sourced directly from Global Caché's [Control Tower
IR database](https://irdb.globalcache.com), you can use them verbatim without any conversion.

A basic [CLI](#cli) is also provided to assist with learning codes directly with the IP2IR.

## Installation

### HACS

1. Install [HACS](https://hacs.xyz)
2. Open HACS → Integrations
3. Open triple-dot menu ( ⠇) → Custom repositories
4. Add this repository's URL (`https://github.com/jaysoffian/ha-itach-ir`), category "Integration"
5. Install this integration and restart Home Assistant

### Manual

Copy `custom_components/itach_ir` into your Home Assistant `custom_components` directory and restart.

## Configuration

Since IR command lists tend to get long, it's best to keep them in [a separate file](./itach_ir.yaml)
and include that file from your `configuration.yaml`:

```yaml
# itach_ir.yaml: Global Caché iTach IR configuration file
# Include in configuration.yaml with:
#   remote:
#     - !include itach_ir.yaml

platform: itach_ir
name: Theater iTach
host: 192.168.1.197       # IP address or hostname of your iTach device
# port: 4998              # Optional, default 4998
devices:
  - name: Theater Denon  # entity name: remote.theater_denon
    commands:
      - name: turn_on
        # Send twice with 0.1s between sends
        data:
          - data: >-
              1:1,0,38000,1,1,129,65,16,16,16,16,16,49,16,16,
              16,49,16,16,16,49,16,16,16,16,16,49,16,2846
            send_count: 2
            interval: 0.1
      - name: turn_off
        data: >-
          1:1,1,38000,1,1,10,30,10,70,10,30,10,30,10,1657
```

Each device becomes a `remote` entity in Home Assistant named after the `name` field.

### Data string format

The `data` value is everything that follows `sendir,` in the iTach API — the
full `sendir,` prefix is added automatically. Format:

```
<connaddr>,<ID>,<freq>,<repeat>,<offset>,<on1>,<off1>,<on2>,<off2>,...
```

- `<connaddr>`: IR port, e.g. `1:1`, `1:2`, or `1:3`
- `<ID>`: any value 0–65535; used to match `completeir` responses
- `<freq>`: carrier frequency in Hz, e.g. `38000`
- `<repeat>`: number of times the iTach hardware repeats the burst, typically `1`
- `<offset>`: index of the first repeat pair (for protocols with a distinct
  header burst), typically `1`; use the actual offset for protocols like JVC
  that have a different header

YAML's folded block scalar (`>-`) is recommended for long strings — internal
newlines are collapsed to spaces before sending, so you can wrap lines freely.

### Simple vs. multi-step commands

A command's `data` field accepts two forms:

**Simple string** — send once:

```yaml
- name: turn_on
  data: "1:1,0,38000,1,1,129,65,16,16,..."
```

**List of steps** — for commands that require multiple sends, repeats, or
pauses between bursts:

```yaml
- name: turn_off
  data:
    - data: "1:1,0,38000,..."
      send_count: 2      # send this IR burst twice
      interval: 0.1      # seconds between repeats (default 0.1)
    - data: "1:1,1,38000,..."
      pause: 0.5         # seconds to wait before this step (default 0)
      send_count: 1
```

Step fields:

| Field | Required | Default | Description |
|---|---|---|---|
| `data` | yes | | GC sendir string |
| `send_count` | no | `1` | Times to send this step |
| `interval` | no | `0.1` | Seconds between repeats within this step |
| `pause` | no | `0` | Seconds to wait before this step |

### Special commands: `turn_on` and `turn_off`

If a device has commands named `turn_on` and/or `turn_off`, they are wired to
the remote entity's power state. Calling `remote.turn_on` or `remote.turn_off`
(or toggling the entity in the UI) sends the corresponding IR command and
updates the entity's on/off state. The state is persisted across Home Assistant
restarts via `RestoreEntity`.

If these commands are not defined, the entity still toggles its state (so it
can be used as a switch in the UI) but no IR is sent.

```yaml
- name: JVC DLA
  commands:
    - name: turn_on
      data: "1:1,0,38000,1,37,319,160,..."
    - name: turn_off
      data: "1:1,0,38000,1,37,319,160,..."
    - name: input_hdmi1
      data: "1:1,0,38000,1,1,..."
```

### Global Caché Control Tower

The easiest way to find GC format codes for your devices is to register at
[irdb.globalcache.com](https://irdb.globalcache.com) and have codes emailed to you. For devices where
the database codes don't work, use the iTach's built-in IR learning mode and copy the learned
`sendir` string directly — no conversion needed.

### Pseudo-commands: `state_on` and `state_off`

The pseudo-commands `state_on` and `state_off` update the entity's on/off state
without sending any IR. This is useful when a device's power state is controlled
by another integration and you want to keep the remote entity in sync.

For example, if a Denon AVR is turned off via the `denonavr` integration (or
becomes unavailable), you can sync the iTach remote's state with an automation:

```yaml
alias: Sync iTach Denon AVR Remote Switch State
description: ""
triggers:
  - trigger: state
    entity_id: media_player.theater_denon_avr
    to:
      - "off"
      - unavailable
actions:
  - action: remote.send_command
    target:
      entity_id: remote.theater_itach_denon_avr
    data:
      command: state_off
```

## Sending commands

Use the standard `remote.send_command` service:

```yaml
service: remote.send_command
target:
  entity_id: remote.theater_denon
data:
  command: turn_on
```

Multiple commands can be sent in one call:

```yaml
service: remote.send_command
target:
  entity_id: remote.theater_denon
data:
  command:
    - turn_on
    - quick_select_1
```

To repeat the entire command sequence at the service call level:

```yaml
service: remote.send_command
target:
  entity_id: remote.theater_denon
data:
  command: turn_on
  num_repeats: 2
  delay_secs: 0.5
```

Note that `num_repeats` and `delay_secs` are handled in software and repeat the entire command
sequence (including all its steps). This is separate from `send_count` within a step, which repeats a
single IR burst, and from the hardware-level repeat controlled by the `<repeat>` field in the data
string.

### Example: Template Light for a Lutron Grafik Eye

Because each iTach device is exposed as a standard `remote` entity, you can layer HA's [template
integrations](https://www.home-assistant.io/integrations/template/) on top to create richer entities.
For example, a Lutron Grafik Eye lighting controller connected via IR can be wrapped as a dimmable
`light` entity using a [template light](https://www.home-assistant.io/integrations/template/#light):

```yaml
light:
  - name: "Theater Grafik Eye"
    unique_id: theater_grafik_eye
    turn_on:
      action: remote.send_command
      target:
        entity_id: remote.theater_itach_lutron_grafik_eye
      data:
        command: "1"
    turn_off:
      action: remote.send_command
      target:
        entity_id: remote.theater_itach_lutron_grafik_eye
      data:
        command: "off"
    set_level:
      action: remote.send_command
      target:
        entity_id: remote.theater_itach_lutron_grafik_eye
      data:
        command: >-
          {% if brightness <= 64 %}4
          {% elif brightness <= 128 %}3
          {% elif brightness <= 191 %}2
          {% else %}1{% endif %}
```

The corresponding iTach device configuration with the learned Grafik Eye IR codes:

```yaml
remote:
  - platform: itach_ir
    host: 192.168.1.197
    devices:
      - name: Theater iTach Lutron Grafik Eye
        commands:
          - name: "1"
            data: >-
              1:1,1,39000,1,1,810,270,90,270,90,90,360,90,90,270,270,90,90,360
          - name: "2"
            data: >-
              1:1,2,39000,1,1,810,270,90,270,90,90,360,90,90,180,90,180,90,540
          - name: "3"
            data: >-
              1:1,3,39000,1,1,810,270,90,270,90,90,360,90,90,180,90,90,90,90,180,360
          - name: "4"
            data: >-
              1:1,4,39000,1,1,810,270,90,270,90,90,360,90,90,90,270,270,90,360
          - name: "off"
            data: >-
              1:1,0,39000,1,1,810,270,90,270,90,90,360,90,90,270,90,180,90,450
```

Together these create a `light.theater_grafik_eye` entity that appears in the HA UI as a dimmable
light. Turning it on sends the Grafik Eye's scene 1 command, turning it off sends the off command,
and the brightness slider maps HA's 0–255 brightness range to Grafik Eye scenes 4 through 1 (dimmest
to brightest). The underlying `remote.send_command` calls resolve to the IR commands defined above.

## Differences from the built-in iTach integration

| | Built-in | This component |
|---|---|---|
| Data format | Pronto hex only | GC sendir natively |
| External dependency | C++ library (`itachip2ir`) | None |
| Learned codes | Must convert to Pronto | Use verbatim |
| Multi-step commands | Not supported | `data` list with `send_count`, `interval`, `pause` |
| `ir_count` / repeats | Device-wide config field | Per-step `send_count` or per-call `num_repeats` |
| Concurrent send protection | None | Per-host lock |

## CLI

A standalone CLI is included for discovery, diagnostics, and learning IR codes directly from the
iTach. The CLI requires [uv](https://docs.astral.sh/uv/).

### Usage

`./cli.py  --help`:

```
 Usage: cli.py [OPTIONS] COMMAND [ARGS]...

╭─ Options ───────────────────────────────────────────────────────────────────────────────────╮
│ --install-completion          Install completion for the current shell.                     │
│ --show-completion             Show completion for the current shell, to copy it or          │
│                               customize the installation.                                   │
│ --help                        Show this message and exit.                                   │
╰─────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ──────────────────────────────────────────────────────────────────────────────────╮
│ discover    Listen for iTach AMX beacon broadcasts.                                         │
│ sendir      Send an IR command string.                                                      │
│ getdevices  Query connected modules.                                                        │
│ getversion  Query firmware version.                                                         │
│ learn       Enter IR learning mode. Press Ctrl-C to stop.                                   │
│ stop-learn  Send stop_IRL (e.g. if learn was left running).                                 │
╰─────────────────────────────────────────────────────────────────────────────────────────────╯
```

### Discovery

All commands except `discover` require `ITACH_HOST` to be set. First, find your device:

`./cli.py discover`:
```
Listening for iTach beacons (10s)...!.........
{
  "UUID": "001DC91234AB",
  "Model": "iTachIP2IR",
  "Revision": "710-1001-05",
  "Pkg_Level": "GCPK001",
  "IP": "192.168.1.197",
  "PN": "025-0026-06",
  "Status": "Ready"
}

export ITACH_HOST=192.168.1.197
```

Then set the environment variable in your shell:

```bash
export ITACH_HOST=192.168.1.197
```

`ITACH_PORT` can also be set if your device uses a non-standard port (default 4998).

### Learning IR codes

Point your remote at the iTach's IR learner port and run:

```bash
./cli.py learn
```

The iTach enters learning mode and prints each captured IR string as it arrives. Press Ctrl-C to
stop. The output is a complete sendir data string that you can paste directly into your
`itach_ir.yaml`.

If the learn session gets stuck, you can force-stop it from another terminal:

```bash
./cli.py stop-learn
```

### Other commands

```bash
./cli.py getversion          # query firmware version
./cli.py getdevices          # list connected modules
./cli.py sendir "38000,1,1,129,65,16,16,..."  # send a raw IR string
./cli.py sendir --ir-port 2 "38000,..."       # send on a different IR port
```

## Credits

Written as a replacement for the built-in Home Assistant `itach` integration.
Inspired by [homebridge-globalcache-itach-ir](https://github.com/jaysoffian/homebridge-globalcache-itach-ir).
