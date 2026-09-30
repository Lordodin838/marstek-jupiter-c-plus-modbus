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

This is the one that bites. Reading 8 registers starting at `0x1008` covers
`0x1008`–`0x100F`. Since `0x100B`–`0x100F` do not exist, the **entire request
fails** with exception 2 — including the three perfectly valid registers at the
front.

The practical consequence for anyone writing a scanner: **a fixed 8-register grid
silently loses registers at every range boundary.** The first scan run against this
device missed `0x0001`–`0x0007` entirely, because the grid block began at `0x0000`
and `0x0000` does not exist. It also lost `0x1008`–`0x100A`, because that block
would have run to `0x100F`.

The fix is to probe a failing block register by register. That is what
[`regscan.py`](../tools/regscan.py) does.

A variant of this was misleading for a long time: a block touching one of the
[slow registers `0x0026`–`0x0029`](register-map.md#the-slow-registers-0x00260x0029)
does not fail with an exception but with silence — if the gateway does not wait
long enough. That is how `0x002A` came to look like an isolated register between
dead neighbours, and why the data block seemed to end at `0x0025`.

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
- Values at addresses that were not being asked for — most likely how the
  phantom readings in older maps came about, such as `800` at `0x0028`: the late
  answer of a [slow register](register-map.md#the-slow-registers-0x00260x0029)
  lands on the next request.
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

The boundary search probes a failing block register by register. That is the
only method that finds an isolated register with dead neighbours on both sides —
and that is exactly what `0x002A` looked like while the EE11 was on *Auto* and
`0x0026`–`0x0029` therefore stayed silent.

`--fast` skips a dead 8-register block after probing only its two ends. On a
device that rejects dead addresses with exception 2 it is much quicker — and
**loses `0x002A` silently**, which a test run
against a simulated device confirmed: 60 registers instead of 61. Worse, the
six addresses in between used to be written into the dump as `exception 2`
although the device was never asked about them. They now read `not probed
(--fast)` instead.

Use `--fast` for a rough boundary check. Never for a dump you intend to diff.

### What a full scan really costs, and why

Two kinds of "nothing here", measured 29 September 2026 on unit 1 and unit 11
alike (details in the [register map](register-map.md#the-slow-registers-0x00260x0029)):

- **Almost every non-existent address is rejected with exception 2**, in about
  0.21 s. Cheap, and never retried — an exception is the device speaking.
- **The four addresses `0x0026`–`0x0029` get no answer** — not because they do
  not exist, but because they take ~3.6 s and the EE11 on *Auto* gives up
  sooner. Afterwards the device needs 2–3 s before it takes the next request.
  That only came out on the evening of 29 September, see
  [Measured timing](#measured-timing).

The second kind is only four addresses, but it sets the floor for the timeout.
A client timeout shorter than the busy time does not merely waste time: the next
request arrives while the device is still occupied, the single-client gateway
refuses the reconnect, and every refused probe is booked as a dead address. On
the test device a 0.3 s timeout lost 7 of 61 registers, `0x002A` among them — and
finished *faster* than a correct run. The scanner now says so in capitals when
it happens.

| Settings | Thorough scan, 3 × 1024 addresses | All 65536 addresses |
|---|---|---|
| defaults: `--timeout 4 --retries 2 --delay 0.45` | ~40 min | ~12 h |
| tuned: `--timeout 4.5 --retries 0 --delay 0.1` | ~20 min | **~6 h** |

The single-register sweep over the whole address space is therefore a night's
work, not a weekend's. **Run on 30 September 2026**, with
[`tools/vollsuche.py`](../tools/vollsuche.py) instead of `regscan.py`: every
address exactly once on its own, EE11 fixed at 5000 ms, client timeout 6 s,
0.1 s pause. 6 h 15 min for 65 536 addresses, 3.1 per second; an exception 2
cost about 0.21 s, as on *Auto*. The result is in the
[register map](register-map.md#everything-else): 65 registers, no others.

The script writes every address to a file as it goes and resumes there after an
interruption, reads a known register every 1024 addresses as a check, and asks
every silent address a second time after a pause. Both were needed: during the
run the EE11 was unreachable three times, for up to seven minutes, and six
addresses stayed silent on the first try — each returned exception 2 on the
second.

**Correction.** An earlier version of this page, written on 29 September, stated
that *every* non-existent address stays silent, put the thorough scan at about
4.4 hours and the full sweep at 84, and claimed `--fast` had no effect on this
device. All three came from generalising the supposed gap `0x0026`–`0x0029`: the
dead addresses that had been looked at closely — `0x0028` in the old dump,
`0x0026` in the calibration — are both in it. `--fast` does work here,
everywhere except at `0x0026`–`0x0029`.

**For judging a gateway change, do not scan at all.** A scan spends its time on
empty address space, which says nothing about the link. `--benchmark` reads the
known blocks over and over instead: minutes, and it mirrors what a client does
in daily use.

---

## Measured timing

`regscan.py --calibrate` measures three things: how fast a real register
answers, what a request to a dead address produces, and how soon after such a
request a fresh client gets a real answer again. Run on 29 September 2026, once
with the EE11's `Modbus TimeOut` on *Auto* and once with a fixed 1000 ms; the
5000 ms column comes from single probes the same evening and from the full
search on the 30th:

| | Auto | fixed 1000 ms | fixed 5000 ms |
|---|---|---|---|
| Response time of a real register, median | 219 ms | 512 ms | — |
| Response time of a real register, max | 330 ms | 620 ms | — |
| Request for `0x0026` | silence, 10 s | silence, 10 s — **no exception 11** | **value, after ~3.6 s** |
| Next real answer possible after that | 2–3 s | 1.5–2 s | at once, a 0.5 s gap was enough |
| Ordinary non-existent address, e.g. `0x0030` | exception 2, 0.21 s | (not measured) | exception 2, ~0.21 s |

**The device, not the gateway, needs the time.** `0x0026`–`0x0029` take 3.6 s
however the gateway is set; the busy time is the device still getting rid of its
late answer after the gateway has long given up. Had the gateway been holding
things up, a fixed 1000 ms would have brought the busy time down to about a
second. It stayed near two. A second observation points the same way: the unit
sweep asked 245 non-existent unit IDs with a 2 s timeout and not one reconnect
was refused. A request addressed to nobody does not occupy the Jupiter, and
neither does a request for an ordinary non-existent register, which comes back
as exception 2 in a fifth of a second. A request for one of the slow registers
does.

**This is probably why the 5 s timeout above fixed things.** A 3 s client
timeout sat right on the edge of a 2–3 s busy time — sometimes enough, under load
often not — whenever a client touched one of the slow registers, as maps that
let the data block run to `0x0027` invited. Those maps were right about that. 5 s cleared it. The fix was found by trial in September;
this is the likeliest mechanism behind it.

**`Modbus TimeOut`: Auto, or fixed at ≥ 4500 ms — nothing in between.** If you
only read up to `0x0025`, *Auto* serves you well. A fixed 1000 ms produced no
exception 11, no shorter busy time, and slower, more scattered answers — at
620 ms uncomfortably close to the limit beyond which the converter would drop a
real answer. To read `0x0026`–`0x0029`, set a fixed 5000 ms and your own client
timeout above it (6–8 s). The full search at 5000 ms showed no slowdown in the
ordinary answers.

What this changed in the scanner: when a block fails, it now goes straight to
single registers instead of halving 8 → 4 → 2 → 1. A dead 8-block costs 9
probes instead of 15, the result is identical, `0x002A` included. On the test
device: 5700 requests down to 3454 for the thorough scan. And `--calibrate` now
measures an ordinary dead address and `0x0026` separately — the first version
used `0x0026` for both, which is how a slow register got mistaken for the rule.

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
