# Home Assistant Global Caché iTach IR (Custom Component)

This integration connects your Global Caché iTach IP2IR to your Home Assistant installation.

This is an alternative to HA's built-in `itach` integration.

This integration accepts the native **sendir** data format, eliminating the lossy Pronto hex
conversion that the built-in integration requires.

If you have learned IR codes, or codes sourced directly from Global Caché's [Control Tower
IR database](https://irdb.globalcache.com), you can use them verbatim without any conversion.

## Requirements

- A [Global Caché iTach](https://www.globalcache.com) IR device (WF2IR, IP2IR,
  or IP2IR-P) accessible on your local network
- Home Assistant running as a Docker container or any other installation method
  that exposes the `/config` directory

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

Add a `remote` platform entry to your `configuration.yaml`:

```yaml
remote:
  - platform: itach
    host: 192.168.1.197       # IP address or hostname of your iTach device
    port: 4998                # optional, default 4998
    devices:
      - name: JVC DLA
        commands:
          - name: turn_on
            data: >-
              1:1,0,38000,1,37,319,160,20,60,20,60,20,20,20,20,
              20,60,20,60,20,60,20,20,20,60,20,20,20,60,20,20,
              20,20,20,20,20,20,20,20,20,898

      - name: Denon
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

      - name: Lumagen
        commands:
          - name: turn_on
            data: >-
              1:1,0,38000,1,1,16,96,16,96,16,192,16,415
          - name: turn_off
            data: >-
              1:1,1,38000,1,1,16,96,16,96,16,192,16,415
```

Each device becomes a `remote` entity in Home Assistant named after the
`name` field, e.g. `remote.jvc_dla`, `remote.denon`, `remote.lumagen`.

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
- name: power_on
  data: "1:1,0,38000,1,1,129,65,16,16,..."
```

**List of steps** — for commands that require multiple sends, repeats, or
pauses between bursts:

```yaml
- name: power_on
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

### Global Caché Control Tower

The easiest way to find GC format codes for your devices is to register at
[irdb.globalcache.com](https://irdb.globalcache.com) and have codes emailed to
you. For devices where the database codes don't work, use the iTach's built-in
IR learning mode and copy the learned `sendir` string directly — no conversion
needed.

## Sending commands

Use the standard `remote.send_command` service:

```yaml
service: remote.send_command
target:
  entity_id: remote.jvc_dla
data:
  command: power_on
```

Multiple commands can be sent in one call:

```yaml
service: remote.send_command
target:
  entity_id: remote.denon
data:
  command:
    - power_on
    - quick_select_1
```

To repeat the entire command sequence at the service call level:

```yaml
service: remote.send_command
target:
  entity_id: remote.denon
data:
  command: power_on
  num_repeats: 2
  delay_secs: 0.5
```

Note that `num_repeats` and `delay_secs` are handled in software and repeat
the entire command sequence (including all its steps). This is separate from
`send_count` within a step, which repeats a single IR burst, and from the
hardware-level repeat controlled by the `<repeat>` field in the data string.

## Differences from the built-in iTach integration

| | Built-in | This component |
|---|---|---|
| Data format | Pronto hex only | GC sendir natively |
| External dependency | C++ library (`itachip2ir`) | None |
| Learned codes | Must convert to Pronto | Use verbatim |
| Multi-step commands | Not supported | `data` list with `send_count`, `interval`, `pause` |
| `ir_count` / repeats | Device-wide config field | Per-step `send_count` or per-call `num_repeats` |
| Concurrent send protection | None | Per-host lock |

## Credits

Written as a replacement for the built-in Home Assistant `itach` integration.
Inspired by [homebridge-globalcache-itach-ir](https://github.com/jaysoffian/homebridge-globalcache-itach-ir).
