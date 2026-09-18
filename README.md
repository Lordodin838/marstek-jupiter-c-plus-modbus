# Marstek Jupiter C Plus — Modbus RTU/TCP field notes

*[Deutsche Fassung: README.de.md](README.de.md)*

Read-only Modbus documentation for the **Marstek Jupiter C Plus** (800 W balcony
storage, 4 PV strings), gathered by scanning the register space of one physical
device over several days and cross-checking every value against a second,
independent data path.

This repository exists because Marstek publishes no register documentation and no
firmware changelogs. Everything here was measured, not read off a datasheet.
Where a reading is a guess, it says so.

**Contains three registers that are not in any public register map**, plus
corrections to the boundaries of the known data block.

---

## Status

| | |
|---|---|
| Device | Jupiter C Plus, 800 W (device type register `0x0025` = 0) |
| Firmware | `142.37.213.110` (EMS 142 / BMS 37 / MPPT 213 / INV 110) |
| Also verified on | `138.37.213.110` — register map byte-for-byte identical |
| Transport | RS485 → Elfin EE11 → Modbus TCP, unit 1, 115200 Bd |
| Function codes | FC3 (read holding registers) only. **FC4 is not supported** — the device answers exception 1 |
| Scope | Read-only. Nothing in this repository writes to the device |
| Sample size | One device. Treat everything as "confirmed on one unit" |

---

## New findings

These three are the reason this repository exists. None of them appear in
[danielrahn/marstek-jupiter-c-plus](https://github.com/danielrahn/marstek-jupiter-c-plus),
which is otherwise the best public map for this device.

### `0x0011` — fault code

Holds the same fault code the device reports over the cloud/MQTT path, **as a
decimal number of the hexadecimal code printed in the manual.**

That conversion is the whole trick, and it is easy to miss:

```
register 0x0011 = 1062   ->   1062 decimal = 0x426   ->   manual: fault 426
```

Confirmed by running the Modbus register and the cloud fault code side by side
through a fault event: in 4 of 4 polls that fell inside the fault window, the
register read 1062 while the cloud reported 426. The single miss was a 3-minute
fault window that fell between two polls — a sampling gap, not a disagreement.

This matters because it gives a **completely cloud-independent alarm path**. The
manufacturer's own app and the MQTT bridge both depend on Marstek's servers; this
register does not.

`0` means no fault. See [`docs/fault-codes.md`](docs/fault-codes.md) for the decode table.

### `0x1100`–`0x1105` — MAC address, as ASCII

Six registers, two ASCII characters each, giving twelve hex digits:

```
0x1100  0x6134  "a4"
0x1101  0x6331  "c1"
0x1102  0x3338  "38"
0x1103  0x3966  "9f"
0x1104  0x3262  "2b"
0x1105  0x3765  "7e"
                 -> a4c1389f2b7e  ->  A4:C1:38:9F:2B:7E
```

Verified against the Bluetooth address the device advertises. Note these are the
*characters* `"a4"`, not the *value* `0xA4` — the registers hold text.

### `0x1200`–`0x1205` — communication module firmware, as ASCII

Same encoding, twelve digits, a build date stamp:

```
-> 202512040647   ->  2025-12-04, build 0647
```

This is the **communication module's** firmware, which is versioned separately
from the four version numbers at `0x001B`–`0x001F`. It does not change when the
EMS firmware is updated, so it is the only way to notice that Marstek has
silently shipped a new comms module build.

---

## Corrections to the known map

Measured against the previously published boundaries:

| Claim | Finding |
|---|---|
| Data block runs to `0x0027` | **No — it ends at `0x0025`.** `0x0026` and `0x0027` do not answer |
| `0x0028` onward mirrors the block | **No — `0x0028` and `0x0029` do not answer** |
| — | **`0x002A` exists**, isolated, with dead registers either side. Value `1`, meaning unknown |

The earlier "values" at `0x0026`/`0x0027` were an artifact of a too-short Modbus
timeout on the TCP gateway — stray responses landing in the wrong request. See
[`docs/gateway.md`](docs/gateway.md); this failure mode is the single biggest
source of wrong data in this setup and it does not announce itself.

A coarse sweep of the **entire 16-bit address space** (two 8-register probes per
256-address page, 244 pages outside the known ranges) found no further responding
pages. Honest limitation: that method finds *blocks*, not isolated registers like
`0x002A`. There may be more single registers hiding in the gaps.

---

## Full register map

See [`docs/register-map.md`](docs/register-map.md) — every address, what it holds,
scaling, and an explicit confidence level for each entry
(**confirmed** / **plausible** / **unknown**).

Two registers remain genuinely unidentified after several days of observation:

- **`0x0023`** — narrow band, 36–39. Ruled out: battery current, battery voltage,
  SoC. Jumped from ~51 to ~37 across a firmware update, which argues against a
  physical measurement.
- **`0x0012`** — stands at 0 permanently. Plausibly a second alarm/warning code
  next to `0x0011`, unproven.

If you have a Jupiter C Plus, comparing these two against a known load or fault
would settle them. Issues welcome.

---

## What's in here

```
docs/register-map.md      Complete register map with confidence levels
docs/fault-codes.md       Fault code table, hex ↔ decimal
docs/gateway.md           RS485 gateway setup and the three device quirks
                          that shape everything else
tools/regscan.py          Dependency-free register scanner and differ
homeassistant/            Example Home Assistant package (native modbus:)
dumps/                    Reference register dump, firmware 142
```

### `tools/regscan.py`

Standard library only, no pymodbus. It builds Modbus TCP frames by hand
specifically so it can validate transaction IDs itself — which is exactly where
cheap RS485-to-Ethernet gateways fail. Reads every value three times and accepts
only a majority verdict; anything else is flagged, not silently used.

```bash
python3 regscan.py --label before-update      # snapshot
python3 regscan.py --sweep                    # also sweep 0x0000-0xFFFF
python3 regscan.py --diff before-update.json  # compare against a snapshot
```

**Take a snapshot before every firmware update.** Marstek ships no changelogs, and
register addresses have demonstrably moved between device generations elsewhere in
the Marstek range. A before/after diff is the only reliable statement about *your*
device.

### `homeassistant/`

A working package for Home Assistant's native `modbus:` integration: all confirmed
registers as sensors, plus a template sensor that decodes `0x0011` into plain text.

The scan intervals in it are not arbitrary — they are prime numbers, deliberately.
[`docs/gateway.md`](docs/gateway.md#why-prime-scan-intervals) explains why that
turned out to matter more than any other single setting.

---

## Credits

- **[danielrahn/marstek-jupiter-c-plus](https://github.com/danielrahn/marstek-jupiter-c-plus)**
  — the base register map this work started from, and the RS485 wiring notes
  (including that pin 3 / +5 V does not appear to be live).
- **[Issue #1 there](https://github.com/danielrahn/marstek-jupiter-c-plus/issues/1)**
  — cell voltages `0x0020`/`0x0021` and the suspected temperature register
  `0x000E`, reported by Lordodin838. Both carried over into the map here.
- **[retris83-ger/Marstek-Jupiter-C-Plus-Modbus-ESPHome](https://github.com/retris83-ger/Marstek-Jupiter-C-Plus-Modbus-ESPHome)**
  — ESPHome implementation for the same device.
- The Marstek threads on **photovoltaikforum.com**, where fault code 426 has its
  own thread titled, fittingly, "Fehlercode 426, der Unbekannte".

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Measurements from a single device. Modbus register addresses can change with
firmware. Nothing here writes to the device, but if you build on it and start
writing: take a snapshot first, and understand that an 800 W inverter tied to your
house wiring is not a thing to poke blindly.
