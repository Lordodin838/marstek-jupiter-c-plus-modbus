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

### A second client is refused, not interleaved

Measured on an Elfin EE11C, firmware 1.41.6, `Max Accept` set to 3: while Home
Assistant held the connection, a second client was **rejected**. The converter
accepted the TCP connection and closed it again immediately — 2700 attempts,
2700 times `connection closed by peer`, at 0.465 s each, which is the client's
own delay and not a timeout. Not one stray value came through.

Two things follow.

**The corrupted readings this repository documents did not come from two
programs competing.** They cannot have: the converter does not let two in. They
came from bursts *inside* one client — several Home Assistant sensors firing in
the same moment. That is what the longer timeout and the prime intervals below
actually fixed, and it is why the fix worked.

**Raising `Max Accept` buys nothing.** The field accepts 3, the device behaves
like 1. Leave it alone.

One consequence for anyone measuring: run the scanner with the other client
stopped, or it will simply be locked out and you will measure nothing. A
`closed` count in the tally is the symptom.

### If you ever need it: take the conversion away from the gateway

Read this as an option, not a recommendation. On the device measured here it
solves a problem that is not occurring — see the baseline at the end of this
page. It becomes interesting when the discard counters start to rise.

In its **Modbus** protocol mode the converter does the TCP↔RTU translation
itself, and the only thing standing between you and a stale answer is a 16-bit
transaction ID that the converter recycles.

Set the converter's protocol to **None / transparent** instead and speak
**Modbus RTU over TCP**: the raw serial frames pass through, each carrying its
own CRC16, and the client controls the framing. `regscan.py --rtu` does this;
Home Assistant's native `modbus:` integration does it with `type: rtuovertcp`.

Be precise about what that buys, because it is easy to oversell:

| | |
|---|---|
| **Caught** | merged frames (two answers in one TCP segment), split or truncated frames, anything shifted by a leftover byte, an answer from another slave address, a byte count that does not match the request |
| **Not caught** | a stale answer to an *earlier request of exactly the same shape*. Its checksum is correct — it is a real frame, just the answer to the wrong question. No protocol-level check sees that, in either mode |

Against the second case there is no clever trick, only discipline: drain the
socket before each request, keep one client on the bus at a time, read several
times and take a majority, and apply the plausibility limits below.

A second, measured argument for RTU: when a frame is corrupted, the RTU client
notices immediately and retries. The Modbus-TCP client cannot — it has to keep
waiting for a matching transaction ID until the timeout expires. Each corrupted
answer therefore costs a full timeout, and that stall is what starts the next
collision. In a test against a simulated gateway that corrupted every second
answer, the TCP path spent one timeout per corruption; the RTU path spent none.

Both modes now count *why* reads were discarded and print the tally at the end
of every run, so a before/after comparison of a gateway change is a number
rather than an impression. The tcp figure is a lower bound by construction: the
failure it cannot detect is the one that motivates the switch.

### What the scanner's `--fast` flag gets wrong on purpose

The boundary search halves failing blocks down to single registers. That is the
only method that finds an isolated register with dead neighbours on both sides —
`0x002A` is exactly that case.

`--fast` skips a dead 8-register block after probing only its two ends. It is
about three times quicker and **loses `0x002A` silently**, which a test run
against a simulated device confirmed: 60 registers instead of 61. Worse, the
six addresses in between used to be written into the dump as `exception 2`
although the device was never asked about them. They now read `not probed
(--fast)` instead.

Use `--fast` for a rough boundary check. Never for a dump you intend to diff.

### What a full scan really costs, and why

**A non-existent address on this device does not answer at all.** It does not
return exception 2 — it stays silent, and the dumps record it as
`keine Antwort: timed out`. Every probe of empty space therefore costs a full
timeout, times the number of retries.

That single fact dominates the runtime, and it is easy to get wrong if you
reason from a simulator that answers with an exception:

| Settings | Cost per dead address | Thorough scan of 3 × 1024 addresses |
|---|---|---|
| `--timeout 4 --retries 2 --delay 0.45` (defaults) | ~13 s | **~15 hours** |
| `--timeout 1 --retries 1 --delay 0.2` | ~2.4 s | ~4 hours |
| as above plus `--fast` | ~2.4 s | ~45 minutes |

The defaults are deliberately cautious, not fast. For a full map, lower the
timeout: a register that exists answers in well under 100 ms, so a short
timeout costs nothing on the addresses you actually care about.

**And for judging a gateway change, do not scan at all.** Over 90 % of a scan's
time is spent waiting on registers that were never there, which says nothing
about the link. `--benchmark` reads the known blocks over and over instead:
minutes rather than hours, and it mirrors what a client does in daily use.

---

## Baseline, 29 September 2026

Elfin EE11C, firmware 1.41.6, Modbus protocol mode, 115200 8N1 half duplex,
`Gap Time` 50 ms, `Modbus TimeOut` auto, Home Assistant stopped for the run:

```
regscan.py --benchmark 40      40 rounds over 9 known blocks
mode = tcp                     requests = 360
failed_attempts = 0            rejects = {}
seconds = 240.7                every block 40/40
```

In the same period the Home Assistant integration reported **0 discarded
values out of 11592 requests**.

Nothing here needs fixing. The point of writing the numbers down is the next
comparison: run the same command after a firmware update, a cable change or a
new client on the network, and the difference is a number instead of an
impression.

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
