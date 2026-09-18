# Register map — Marstek Jupiter C Plus

*[Deutsche Fassung: register-map.de.md](register-map.de.md)*

Firmware `142.37.213.110`, identical on `138.37.213.110`.
All registers are **holding registers, read with FC3**. FC4 (input registers) is
not supported — the device answers exception 1.

Word order for 32-bit values is **big endian, high word first**. No byte or word
swap is needed.

## Confidence levels

| Level | Meaning |
|---|---|
| **confirmed** | Cross-checked against an independent source (cloud/MQTT path, a second meter, or an arithmetic identity), or self-evidently correct |
| **plausible** | Behaves consistently with the stated meaning over days of observation, but nothing independent confirms it |
| **unknown** | Responds, value recorded, meaning not established |

---

## Data block `0x0001`–`0x0025`

| Addr | Meaning | Type | Scale | Unit | Confidence |
|---|---|---|---|---|---|
| `0x0001` | PV1 voltage | u16 | 0.1 | V | confirmed |
| `0x0002` | PV1 current | u16 | 0.1 | A | confirmed |
| `0x0003` | PV1 power | u16 | 1 | W | confirmed |
| `0x0004` | PV2 voltage | u16 | 0.1 | V | confirmed |
| `0x0005` | PV2 current | u16 | 0.1 | A | confirmed |
| `0x0006` | PV2 power | u16 | 1 | W | confirmed |
| `0x0007` | PV3 voltage | u16 | 0.1 | V | confirmed |
| `0x0008` | PV3 current | u16 | 0.1 | A | confirmed |
| `0x0009` | PV3 power | u16 | 1 | W | confirmed |
| `0x000A` | PV4 voltage | u16 | 0.1 | V | confirmed |
| `0x000B` | PV4 current | u16 | 0.1 | A | confirmed |
| `0x000C` | PV4 power | u16 | 1 | W | confirmed |
| `0x000D` | Grid power | **i16** | 1 | W | confirmed |
| `0x000E` | Temperature (internal/ambient) | u16 | 0.1 | °C | plausible |
| `0x000F` | Battery voltage | u16 | 0.1 | V | confirmed |
| `0x0010` | Battery SoC | u16 | 1 | % | confirmed |
| `0x0011` | **Fault code** | u16 | 1 | — | confirmed — [see below](#0x0011--fault-code) |
| `0x0012` | Suspected second alarm/warning code | i16 | 1 | — | unknown |
| `0x0013`+`0x0014` | Daily generation | u32 | 0.01 | kWh | confirmed |
| `0x0015`+`0x0016` | Monthly generation | u32 | 0.01 | kWh | confirmed |
| `0x0017`+`0x0018` | Daily grid feed-in | u32 | 0.01 | kWh | confirmed |
| `0x0019`+`0x001A` | Monthly grid feed-in | u32 | 0.01 | kWh | confirmed |
| `0x001B` | Device ID | u16 | 1 | — | confirmed |
| `0x001C` | EMS firmware version | u16 | 1 | — | confirmed |
| `0x001D` | INV firmware version | u16 | 1 | — | confirmed |
| `0x001E` | MPPT firmware version | u16 | 1 | — | confirmed |
| `0x001F` | BMS firmware version | u16 | 1 | — | confirmed |
| `0x0020` | Cell voltage, maximum | u16 | 0.001 | V | confirmed |
| `0x0021` | Cell voltage, minimum | u16 | 0.001 | V | confirmed |
| `0x0022` | Screen firmware version | u16 | 1 | — | confirmed |
| `0x0023` | — | u16 | ? | ? | **unknown** — [see below](#0x0023--unidentified) |
| `0x0024` | — | u16 | ? | ? | unknown, constant 0 |
| `0x0025` | Device type (`0` = Jupiter C 800 W) | u16 | 1 | — | confirmed |

**`0x000D` is signed.** Reading it as u16 gives nonsense the moment the device
imports rather than exports. The sign convention follows the CT: positive means
power flowing in the direction the clamp counts as import.

**`0x0020` × 16 ≈ `0x000F`.** That identity is what confirms both: 16 cells in
series, maximum cell voltage times 16 tracks the pack voltage across the whole
range.

**There is no DC battery power register.** Battery power has to be derived
(PV power minus grid power, or pack voltage times a current you do not have here).

**Depth of discharge / SoC floor is not exposed on Modbus.** Firmware 140+ added a
settable discharge limit, but it is reachable only through the app and the
cloud/MQTT path. Nothing appeared in the register map across a 138 → 142 update —
confirmed by a byte-for-byte diff of a full register dump taken before and after.

## Gap `0x0026`–`0x0029`

Do not answer. Any read that overlaps them fails completely, including a read that
starts inside the valid block — see [gateway.md](gateway.md#the-three-device-quirks).

Earlier maps listed values at `0x0026`/`0x0027`. Those were stray responses from a
desynchronised TCP gateway, not device data.

## Isolated register `0x002A`

| Addr | Meaning | Type | Value seen | Confidence |
|---|---|---|---|---|
| `0x002A` | — | u16 | `1` | unknown |

Isolated: `0x0029` before it and `0x002B` after it are both dead. It appears in no
published register map. Constant `1` over days of observation.

Worth knowing methodologically: a block-probing scan **cannot** find this register,
because every 8-register probe overlapping it also overlaps dead addresses and
therefore fails entirely. It only turns up if the scanner halves failing blocks
down to single registers.

`0x002B`–`0x00FF`: no response.

---

## Status block `0x1000`–`0x100A`

| Addr | Meaning | Value seen | Confidence |
|---|---|---|---|
| `0x1000` | status flag | 1 | unknown |
| `0x1001` | status flag | 1 and **2** | unknown |
| `0x1002` | status flag | 1 | unknown |
| `0x1003` | status flag | 1 | unknown |
| `0x1004` | PV1 working status | 0/1 | confirmed |
| `0x1005` | PV2 working status | 0/1 | confirmed |
| `0x1006` | PV3 working status | 0/1 | confirmed |
| `0x1007` | PV4 working status | 0/1 | confirmed |
| `0x1008` | Inverter working status | 0/1 | confirmed |
| `0x1009` | status flag | 0 and 1 | unknown |
| `0x100A` | status flag | 0 | unknown |

`0x1001` has been seen holding **2**, not just 0 or 1. Whatever this block is, it
is not purely boolean, so do not map it blindly onto binary sensors the way
`0x1004`–`0x1008` can be. `0x1001` and `0x1009` both change over time, so they
carry something; nobody has worked out what.

The PV status flags follow daylight exactly (all four at 0 at night), which is
what confirms them.

`0x100B`–`0x10FF`: no response.

---

## MAC address `0x1100`–`0x1105`

Example device, address `A4:C1:38:9F:2B:7E`:

| Addr | Content | ASCII |
|---|---|---|
| `0x1100` | `0x6134` | `a4` |
| `0x1101` | `0x6331` | `c1` |
| `0x1102` | `0x3338` | `38` |
| `0x1103` | `0x3966` | `9f` |
| `0x1104` | `0x3262` | `2b` |
| `0x1105` | `0x3765` | `7e` |

Twelve ASCII characters, lower-case hex, high byte first within each register.
Assembled: `a4c1389f2b7e` → `A4:C1:38:9F:2B:7E`.

Confirmed against the Bluetooth address the device advertises — that
cross-check is what makes this register certain rather than merely plausible.

`0x1106`–`0x11FF`: no response.

---

## Communication module firmware `0x1200`–`0x1205`

Same encoding: twelve ASCII digits, assembling to a build stamp.

```
202512040647   ->   2025-12-04, build 0647
```

Versioned **separately** from `0x001B`–`0x001F` and unchanged by an EMS firmware
update. The only way to detect a new comms module build.

`0x1206`–`0x13FF`: no response.

---

## Write registers `0x4000`+

`0x4000`–`0x43FF` return no data on FC3. Presumed write-only control registers, by
analogy with other devices in the Marstek range. **Not investigated** — everything
in this repository is read-only by design, and probing write registers on a
grid-tied inverter is a good way to find out what an undocumented write does.

---

## Everything else

A coarse sweep of the full 16-bit address space (`0x0000`–`0xFFFF`), two
8-register probes per 256-address page, 244 pages outside the ranges above:
**no further page responds.**

Limitation, stated plainly: this finds blocks, not isolated registers. `0x002A`
proves such registers exist. The gaps may hold more.

---

## `0x0011` — fault code

The register holds the **decimal value of the hexadecimal fault code** printed in
the manual:

| Register reads | Hex | Manual calls it |
|---|---|---|
| `0` | — | no fault |
| `1028` | `0x404` | grid-side overheat protection |
| `1062` | `0x426` | *undocumented — gap between 422 and 440 in the manual* |
| `1483` | `0x5CB` | network anomaly |

Full table: [fault-codes.md](fault-codes.md).

Evidence: during a fault event, the Modbus register and the cloud fault code were
logged side by side. In 4 of 4 polls inside the fault window the register read
1062 while the cloud reported 426. One 3-minute fault window produced no matching
Modbus sample — it fell between two 10-minute polls. A sampling gap, not a
contradiction. Poll this faster than 60 s if you want to catch short faults.

## `0x0023` — unidentified

Observed band 36–39, narrow and slow-moving. Ruled out by direct comparison:

- **not battery current** — does not track charge/discharge transitions
- **not battery voltage** — does not track `0x000F`
- **not SoC** — does not track `0x0010`

It jumped from ~51 to ~37 across the 138 → 142 firmware update and settled into
its new band. A physical measurement should not do that. Possibly a counter, an
internal state code, or a calibration value.

If you have this device: reporting the value alongside your pack temperature and
cell count would help.
