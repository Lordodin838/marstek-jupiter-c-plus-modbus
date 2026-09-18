# Gateway, wiring, and the quirks that shape everything

*[Deutsche Fassung: gateway.de.md](gateway.de.md)*

This is the part that costs people days. The register map is the easy half; making
the bus give you *correct* values is the hard half, and wrong values do not
announce themselves.

---

## Wiring

RS485 on the Jupiter C Plus. Per
[danielrahn's notes](https://github.com/danielrahn/marstek-jupiter-c-plus),
**pin 3 (+5 V) does not appear to be live** — power your converter separately.

Only A/B/GND are needed.

## Converter: Elfin EE11 (RS485 → Ethernet)

Working settings:

| Setting | Value |
|---|---|
| Baud rate | 115200 |
| Protocol | Modbus TCP |
| Port | 502 |
| Route | UART |
| Modbus unit / slave ID | 1 |

Any RS485-to-TCP converter will do. The EE11 is what this was measured on, and its
failure mode — described below — is common to the whole cheap-converter class.

---

## The three device quirks

Everything else in this repository is shaped by these. Learn them before writing
your own client.

### 1. Maximum 8 registers per request

Ask for 9 or more and the device answers **exception 3 (illegal data value)**.
Not a partial read — a refusal.

### 2. A request that overruns a valid range fails *completely*

This is the one that bites. Reading 8 registers starting at `0x0020` covers
`0x0020`–`0x0027`. Since `0x0026` and `0x0027` do not exist, the **entire request
fails** — including the six perfectly valid registers at the front.

The practical consequence for anyone writing a scanner: **a fixed 8-register grid
silently loses registers at every range boundary.** The first scan run against this
device missed `0x0001`–`0x0007` entirely, because the grid block began at `0x0000`
and `0x0000` does not exist. It also lost `0x1008`–`0x100A`, because that block
would have run to `0x100F`.

The fix is to halve failing blocks down to single registers. That is what
[`regscan.py`](../tools/regscan.py) does, and it is why `0x002A` was found at all.

### 3. FC4 is not supported

Input registers (function code 4) answer **exception 1 (illegal function)**.
Everything is a holding register, read with FC3.

---

## The failure mode that produces wrong data

**The converter passes foreign responses through when the transaction ID matches.**

The Elfin handles one request at a time. Under load — several clients, or one
client polling fast — a response to request N can arrive after the client has
already given up on it and moved to request N+1. If the transaction IDs line up,
the client accepts it. **The value lands in the wrong sensor.**

What this looks like in practice:

- A state-of-charge sensor reading `3255` %.
- Registers that "have values" but should be dead — this is where the phantom
  `0x0026` / `0x0027` readings in older maps came from.
- Values that look plausible but belong to a neighbouring register, which is far
  worse, because nothing flags them.

It does not throw an error. It does not log anything. You find it by noticing that
a daily minimum/maximum is impossible.

### What actually fixed it

Three changes, in order of how much they mattered:

**1. Raise the timeout.** 3 s was too short: the client gave up while a response
was still in flight, and that response then collided with the next request.
**5 s fixed it.** This was the single biggest improvement.

**2. De-align the polling — prime scan intervals.** See below.

**3. Do not mix 1-register and 2-register (uint32) reads at the same interval.**
Staggering them helps on its own.

Result, measured: request rate dropped from ~76/min to ~48/min, the bursts of 22
simultaneous requests disappeared, and the implausible-value count over the
following four days was **zero** — against daily SoC ranges of 0–3255 and 0–3308
in the two days before the change.

### Why prime scan intervals

If several sensors poll at 10 s, 20 s, 30 s and 60 s, then every 60 s **all of
them fire at once**. That burst is exactly the condition the converter cannot
handle, and it recurs on a predictable schedule, so the corruption is periodic and
looks like a device fault.

Give each sensor a different prime-numbered interval — 31, 61, 67, 127, 197, 199,
211, 223, 227, 293, 307, 311, 313 s — and the bursts stop reassembling. Two
intervals with no common factor only coincide once every *n×m* seconds instead of
every *max(n,m)*.

Only the handful of registers you actually control against need to be fast. In the
example package, six registers poll at 10 s (four PV power, grid power, SoC);
everything else is at a prime between 31 s and ~1 hour.

### Countermeasure in the scanner

[`regscan.py`](../tools/regscan.py) builds its Modbus frames by hand rather than
using pymodbus, specifically so it can **validate the transaction ID, protocol ID,
unit and length itself** and discard foreign responses — up to four per read —
instead of trusting them.

On top of that it reads everything three times and accepts a value only if it
appeared at least twice. Anything else is written to the report as `NEIN`/unsafe
rather than quietly used. That belt-and-braces approach is why the dumps in
[`dumps/`](../dumps) can be diffed against each other meaningfully.

---

## Sanity checks worth automating

After any change — firmware, wiring, polling — check these. They catch a shifted
register map immediately:

| Check | Expected |
|---|---|
| SoC (`0x0010`) | 0–100, never above |
| Battery voltage (`0x000F`) | around 52 V for a 16S LFP pack |
| Cell voltages (`0x0020`/`0x0021`) | around 3.3 V, max ≥ min |
| `0x0020` × 16 | ≈ `0x000F` |
| Device type (`0x0025`) | `0`, constant. **If this changes, the map has shifted** |
| PV status flags (`0x1004`–`0x1007`) | all 0 at night |

Daily min/max statistics are the cheapest detector: an impossible maximum on any
sensor means the bus is desynchronising, even if the current value looks fine.
