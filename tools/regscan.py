#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Register scanner for the Marstek Jupiter C Plus  --  Modbus TCP
===============================================================

Purpose
-------
Take a complete snapshot of the readable register space BEFORE a firmware
update, then take the same snapshot again afterwards. Diffing the two files
states in black and white whether Marstek moved, removed or added registers.

Background: Marstek publishes no changelogs, and register addresses have
demonstrably changed between device generations elsewhere in the Marstek
range. A before/after diff is the only reliable statement about YOUR device.

Usage
-----
    python3 regscan.py --host 192.168.1.50
    python3 regscan.py --label before-update      # meaningful file name
    python3 regscan.py --sweep                    # also probe the entire
                                                  # 16-bit address space
    python3 regscan.py --diff old.json            # hold a fresh scan
                                                  # against an old one
    python3 regscan.py --diff a.json --diff-only b.json
                                                  # compare two existing
                                                  # files without reading
                                                  # the device
    python3 regscan.py --rtu                      # speak Modbus RTU over
                                                  # TCP -- see below

THIS SCRIPT NEVER WRITES TO THE DEVICE. It uses function code 3 (read
holding registers) exclusively.

Three device properties that explain the design
-----------------------------------------------
1. At most 8 registers per request; above that, exception 3.

2. A request that reaches even partially past the end of a valid range
   fails COMPLETELY. A fixed 8-register grid therefore loses the registers
   at range boundaries: the first run against this device never saw
   0x0001-0x0007, because the block started at 0x0000 and 0x0000 does not
   exist. Likewise 0x1008-0x100A dropped out, because that block would have
   reached to 0x100F.
   Hence the two-phase scan: find the boundaries first (a block that
   fails is probed register by register), then read the discovered ranges.

3. The RS485-to-Ethernet gateway handles one request at a time and will
   pass foreign responses through when the transaction ID matches. While
   another client polls the same device in parallel, single measurements
   cannot be trusted.
   Countermeasure: every value is read several times and only a majority
   verdict is accepted. Anything else is flagged as unsafe rather than
   silently used.

Two modes: --rtu is the stronger one
------------------------------------
Default is Modbus TCP. The gateway converts to RTU itself, and the only
thing standing between you and a stale answer is a 16-bit transaction ID
that the gateway recycles. When it matches by accident, the wrong value
lands in the right-looking register and nothing anywhere reports a fault.

With --rtu the script speaks Modbus RTU over TCP: raw serial frames, each
with its own CRC16, and the socket is drained before every request.
Requires the gateway's protocol setting to be "None"/transparent instead of
"Modbus" -- on an Elfin EE11/EW11 that is one dropdown, and Home Assistant's
native modbus: integration follows with type: rtuovertcp.

Be precise about what that buys, because it is easy to oversell:

  CAUGHT -- merged frames (two answers in one TCP segment), split or
  truncated frames, anything shifted by a leftover byte, an answer from
  another slave address, and an answer whose byte count does not match the
  request. Those are the failures a cheap gateway actually produces, and in
  "Modbus" mode the gateway resolves them silently and sometimes wrongly,
  where here they fail loudly.

  NOT CAUGHT -- a stale answer to an earlier request of exactly the same
  shape. Its checksum is correct, because it is a genuine frame; it is just
  the answer to the wrong question. No protocol-level check can see that,
  in either mode. What guards against it is the drain, one client on the
  bus at a time, the three passes with a majority verdict, and the
  plausibility limits in docs/gateway.md.

Both modes count why reads were discarded and print the tally at the end
(also written into the dump). Running the same scan once per mode is the
measurement of whether the change helped -- and note that the tcp figure is
a lower bound by construction: the failure it cannot detect is the one that
makes --rtu worth it.

RTU has no transaction IDs, so the socket is drained before every request;
a single leftover byte would shift all following frames. Those leftovers
are counted too, under "stale_bytes".

On the coarse sweep (--sweep)
-----------------------------
The thorough scan only covers RANGES. --sweep additionally probes the whole
16-bit address space: two 8-register samples per 256-address page. If either
answers, the whole page is then scanned thoroughly.

HONEST LIMITATION of that method: it finds blocks, not loners. A single
valid register between dead neighbours -- such as 0x002A -- makes every
8-register probe fail and stays invisible. That is exactly how the first
full scan missed 0x002A. To find isolated registers, put the range into
RANGES, where a failing block is probed register by register.
"""

import argparse
import json
import os
import socket
import struct
import sys
import time
from datetime import datetime

# --- Installation defaults (override on the command line) -------------
HOST = "192.168.1.50"       # RS485-to-Ethernet gateway, e.g. Elfin EE11
PORT = 502
UNIT = 1                    # Modbus slave address of the Jupiter

# --- Sampling ---------------------------------------------------------
BLOCK = 8                   # device limit, do not raise
PASSES = 3                  # passes for the majority verdict
DELAY = 0.45                # seconds between two requests. Deliberately
                            # slow: the bus should carry only a little
                            # extra load next to your normal polling.
TIMEOUT = 4.0
RETRY = 2                   # retries on timeout

# Ranges scanned thoroughly -- a failing block is probed register by
# register here, so
# isolated single registers are found too. Widened from 0x100 to 0x400
# each after 0x002A showed that Marstek also places registers outside the
# known blocks.
RANGES = [
    (0x0000, 0x03FF, "data block"),
    (0x1000, 0x13FF, "status flags"),
    (0x4000, 0x43FF, "write registers (usually empty when read)"),
]

# Blocks known to hold registers on a Jupiter C Plus, firmware 142. The
# benchmark hammers these instead of scanning dead space: a dead address
# costs a full timeout and says nothing about the quality of the link.
KNOWN_BLOCKS = [
    (0x0001, 8), (0x0009, 8), (0x0011, 8), (0x0019, 8), (0x0021, 5),
    (0x1000, 8), (0x1008, 3), (0x1100, 6), (0x1200, 6),
]

# Coarse sweep: page size and sample offsets within the page.
COARSE_STEP = 0x0100
COARSE_PROBES = (0x00, 0x80)

OUTDIR = os.path.dirname(os.path.abspath(__file__))


def crc16(frame):
    """Modbus RTU checksum. Returned low byte first, as it goes on the wire."""
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return struct.pack("<H", crc)


class Bus:
    """Modbus, deliberately without a library, in two modes.

    mode "tcp"  -- Modbus TCP. The gateway converts to RTU itself. The only
                   protection against a stray response is the transaction
                   ID, which the gateway recycles, so a stale answer whose
                   ID happens to match is accepted as genuine.

    mode "rtu"  -- Modbus RTU over TCP. The gateway passes raw serial frames
                   through (its protocol setting must be "None"/transparent,
                   not "Modbus"). Every frame then carries a CRC16, so a
                   stray or truncated response is caught with near certainty
                   instead of being guessed at.

    Raw sockets either way, so that the checks happen here rather than
    inside a library that trusts the gateway.
    """

    def __init__(self, host, port, unit, timeout, mode="tcp"):
        if mode not in ("tcp", "rtu"):
            raise ValueError("mode must be 'tcp' or 'rtu'")
        self.host, self.port, self.timeout = host, port, timeout
        self.unit = unit
        self.mode = mode
        self.sock = None
        self.tid = 0
        self.reads = 0
        # Why a read was discarded. This is the measurement that says
        # whether switching the gateway to transparent mode was worth it.
        self.rejects = {
            "crc": 0,             # rtu: checksum failed
            "slave": 0,           # answer from a different slave address
            "function": 0,        # function code not 3 and not an exception
            "length": 0,          # byte count does not match the request
            "foreign_tid": 0,     # tcp: transaction ID of another request
            "stale_bytes": 0,     # bytes left over before a request
            "timeout": 0,         # silence until the timeout expired
            "closed": 0,          # the gateway dropped the connection
            "exception": 0,       # device said no -- not an error
        }
        # Attempts that came back without a value for a reason other than
        # the device saying no. One disturbance can trip several of the
        # counters above (a foreign answer in tcp mode also costs a
        # timeout), so the percentage is based on this instead.
        self.failed = 0
        self.connect()

    def connect(self):
        self.close()
        self.sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)

    def _blame(self, exc):
        """Silence and a dropped connection are different diagnoses.

        Silence means the address does not exist, or the device is busy.
        A drop means the gateway refused to talk to us at all -- typically
        because another client already holds it. Counting both as "timeout"
        sends you looking for a bus problem when the answer is that somebody
        else is on the line.
        """
        if isinstance(exc, socket.timeout):
            self.rejects["timeout"] += 1
        else:
            self.rejects["closed"] += 1

    def drain(self):
        """Discard anything still pending before sending a new request.

        In RTU mode there is no transaction ID, so a leftover byte from a
        previous exchange would shift every following frame by one and turn
        a perfectly good answer into a CRC error. Cheap insurance, and the
        counter doubles as a symptom report.
        """
        self.sock.setblocking(False)
        dropped = 0
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                dropped += len(chunk)
        except (BlockingIOError, OSError):
            pass
        finally:
            self.sock.setblocking(True)
            self.sock.settimeout(self.timeout)
        if dropped:
            self.rejects["stale_bytes"] += dropped
        return dropped

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed by peer")
            buf += chunk
        return buf

    def _read_once(self, addr, count):
        if self.mode == "rtu":
            return self._read_once_rtu(addr, count)
        return self._read_once_tcp(addr, count)

    def _read_once_rtu(self, addr, count):
        """Modbus RTU over TCP: raw serial frame, checked by CRC."""
        self.drain()
        req = struct.pack(">BBHH", self.unit, 3, addr, count)
        req += crc16(req)

        try:
            self.sock.sendall(req)
        except OSError as exc:
            self.connect()
            return None, "send error: %s" % exc

        try:
            head = self._recv_exact(3)          # slave, function, third byte
        except (OSError, ConnectionError) as exc:
            self._blame(exc)
            self.connect()
            return None, "no response: %s" % exc

        slave, func, third = head[0], head[1], head[2]

        if func & 0x80:                          # exception frame, 5 bytes
            try:
                rest = self._recv_exact(2)
            except (OSError, ConnectionError) as exc:
                self.connect()
                return None, "response truncated: %s" % exc
            if crc16(head) != rest:
                self.rejects["crc"] += 1
                self.drain()
                return None, "crc error"
            if slave != self.unit:
                self.rejects["slave"] += 1
                return None, "answer from slave %d" % slave
            self.rejects["exception"] += 1
            return None, "exception %d" % third

        try:
            rest = self._recv_exact(third + 2)   # payload + crc
        except (OSError, ConnectionError) as exc:
            self.rejects["length"] += 1
            self.connect()
            return None, "response truncated: %s" % exc

        if crc16(head + rest[:third]) != rest[third:]:
            self.rejects["crc"] += 1
            self.drain()
            return None, "crc error"
        if slave != self.unit:
            self.rejects["slave"] += 1
            return None, "answer from slave %d" % slave
        if func != 3:
            self.rejects["function"] += 1
            return None, "unexpected function code %d" % func
        if third != count * 2:
            self.rejects["length"] += 1
            return None, "length mismatch (%d instead of %d)" % (
                third, count * 2)

        return list(struct.unpack(">%dH" % count, rest[:third])), None

    def _read_once_tcp(self, addr, count):
        self.tid = (self.tid % 65530) + 1
        tid = self.tid
        req = struct.pack(">HHHBBHH", tid, 0, 6, self.unit, 3, addr, count)

        try:
            self.sock.sendall(req)
        except OSError as exc:
            self.connect()
            return None, "send error: %s" % exc

        # Discard up to four responses whose transaction ID does not
        # match. Those are the strays belonging to somebody else's
        # request -- accepting them silently is exactly the mistake that
        # swaps values between registers.
        for _ in range(4):
            try:
                head = self._recv_exact(6)
            except (OSError, ConnectionError) as exc:
                self._blame(exc)
                self.connect()
                return None, "no response: %s" % exc

            rtid, pid, length = struct.unpack(">HHH", head)
            if length < 2 or length > 260:
                self.rejects["length"] += 1
                self.connect()
                return None, "implausible length %d" % length

            try:
                body = self._recv_exact(length)
            except (OSError, ConnectionError) as exc:
                self.rejects["length"] += 1
                self.connect()
                return None, "response truncated: %s" % exc

            if rtid != tid or pid != 0:
                self.rejects["foreign_tid"] += 1
                continue                      # foreign response, keep waiting
            if body[0] != self.unit:
                self.rejects["slave"] += 1
                continue

            func = body[1]
            if func == 0x83:
                self.rejects["exception"] += 1
                return None, "exception %d" % body[2]
            if func != 3:
                self.rejects["function"] += 1
                return None, "unexpected function code %d" % func

            nbytes = body[2]
            payload = body[3:3 + nbytes]
            if nbytes != count * 2 or len(payload) != nbytes:
                self.rejects["length"] += 1
                return None, "length mismatch (%d instead of %d)" % (
                    nbytes, count * 2)

            return list(struct.unpack(">%dH" % count, payload)), None

        return None, "only foreign responses received"

    def raw(self, pdu, unit=None, settle=0.15):
        """Send an arbitrary PDU and hand back the answer, unparsed.

        read() knows only function code 3. This is for asking the device
        what else it understands -- device identification, coils, the
        report-server-id call. Returns (pdu_bytes, None) or (None, reason).

        In RTU mode the length of an answer to an unknown function cannot
        be derived in advance, so bytes are collected until the line has
        been quiet for `settle` seconds, then the checksum decides whether
        what arrived is a whole frame.
        """
        unit = self.unit if unit is None else unit
        self.reads += 1

        if self.mode == "rtu":
            self.drain()
            frame = bytes([unit]) + pdu
            frame += crc16(frame)
            try:
                self.sock.sendall(frame)
            except OSError as exc:
                self.connect()
                return None, "send error: %s" % exc
            buf = b""
            deadline = time.time() + self.timeout
            self.sock.settimeout(settle)
            try:
                while time.time() < deadline:
                    try:
                        chunk = self.sock.recv(512)
                    except socket.timeout:
                        if buf:
                            break              # line went quiet, frame done
                        continue
                    if not chunk:
                        break
                    buf += chunk
            except OSError as exc:
                self.connect()
                return None, "no response: %s" % exc
            finally:
                self.sock.settimeout(self.timeout)
            if not buf:
                self.rejects["timeout"] += 1
                return None, "no response"
            if len(buf) < 4 or crc16(buf[:-2]) != buf[-2:]:
                self.rejects["crc"] += 1
                return None, "crc error (%d bytes)" % len(buf)
            if buf[0] != unit:
                self.rejects["slave"] += 1
                return None, "answer from slave %d" % buf[0]
            return buf[1:-2], None

        # Modbus TCP
        self.tid = (self.tid % 65530) + 1
        tid = self.tid
        req = struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu
        try:
            self.sock.sendall(req)
        except OSError as exc:
            self.connect()
            return None, "send error: %s" % exc
        try:
            head = self._recv_exact(6)
            rtid, pid, length = struct.unpack(">HHH", head)
            if length < 2 or length > 260:
                self.connect()
                return None, "implausible length %d" % length
            body = self._recv_exact(length)
        except (OSError, ConnectionError) as exc:
            self._blame(exc)
            self.connect()
            return None, "no response: %s" % exc
        if rtid != tid or pid != 0:
            self.rejects["foreign_tid"] += 1
            return None, "foreign answer"
        if body[0] != unit:
            self.rejects["slave"] += 1
            return None, "answer from slave %d" % body[0]
        return body[1:], None

    def send_only(self, pdu, unit=None):
        """Put a request on the wire and walk away. Used by the calibration
        to reproduce a client that gives up early."""
        unit = self.unit if unit is None else unit
        if self.mode == "rtu":
            frame = bytes([unit]) + pdu
            frame += crc16(frame)
        else:
            self.tid = (self.tid % 65530) + 1
            frame = struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, unit) + pdu
        self.sock.sendall(frame)

    def read(self, addr, count):
        """Read with retries. Exceptions are NOT retried -- they are a
        genuine statement by the device, not a glitch."""
        note = None
        for _ in range(RETRY + 1):
            self.reads += 1
            vals, note = self._read_once(addr, count)
            time.sleep(DELAY)
            if vals is not None:
                return vals, None
            if note and note.startswith("exception"):
                return None, note
            self.failed += 1
        return None, note


def discover(bus, start, end, fast=False):
    """Find the actual boundaries of valid ranges.

    A block reaching even partially into nothing fails entirely. So: when
    a block fails, probe each of its registers on its own.
    That is the only method that also finds an isolated register with dead
    neighbours on both sides, such as 0x002A.

    fast=True enables a shortcut for large empty zones: if an 8-register
    block reports exception 2 (illegal address) and its first and last
    register report exception 2 individually as well, the whole block counts
    as empty. It saves roughly two thirds of the requests --

    -- and it is WRONG in exactly the case this repository exists for. A
    register sitting alone inside such a block is never probed, and the six
    addresses in between are written into the dump as "exception 2" although
    the device never said so about them. 0x002A is precisely that case, and
    a test run with the shortcut on loses it silently. Hence: off by default,
    and behind --fast for anyone who only wants a rough boundary check.
    """
    valid, dead = [], {}
    todo = []
    addr = start
    while addr <= end:
        todo.append((addr, min(BLOCK, end - addr + 1)))
        addr += BLOCK

    last = time.time()
    span = max(1, end - start)
    while todo:
        a, n = todo.pop(0)
        # A thorough range takes a quarter of an hour. Without a sign of
        # life every so often that looks exactly like a hang, and the
        # natural reaction is to abort a run that was working.
        if time.time() - last >= 20:
            # Position in the range, not a count of settled addresses:
            # failed blocks are re-queued as singles, so counting them
            # double would produce a progress line reading "1220 of 1024".
            print("    ... at 0x%04X (%d%% through the range), "
                  "%d requests so far, %d addresses answering"
                  % (a, 100 * (a - start) // span, bus.reads, len(valid)),
                  flush=True)
            last = time.time()
        vals, note = bus.read(a, n)
        if vals is not None:
            valid.extend(range(a, a + n))
            continue
        if n == 1:
            dead[a] = note
            continue
        if fast and note == "exception 2" and n == BLOCK:
            v1, n1 = bus.read(a, 1)
            v2, n2 = bus.read(a + n - 1, 1)
            if v1 is None and n1 == "exception 2" and \
               v2 is None and n2 == "exception 2":
                dead[a] = "exception 2"
                dead[a + n - 1] = "exception 2"
                for x in range(a + 1, a + n - 1):
                    # Not probed. Saying "exception 2" here would put words
                    # into the device's mouth.
                    dead[x] = "not probed (--fast)"
                continue
            if v1 is not None:
                valid.append(a)
            if v2 is not None:
                valid.append(a + n - 1)
            if n > 2:
                todo.insert(0, (a + 1, n - 2))
            continue
        # Straight to single registers instead of halving 8 -> 4 -> 2 -> 1.
        # On this device a dead address does not answer at all, and the
        # device then needs about two seconds before it takes the next
        # request (measured 29.09.2026). Every failing probe therefore costs
        # a full timeout. Halving spends 15 probes on a dead 8-block
        # (1 + 2 + 4 + 8); going straight to singles spends 9 (1 + 8), and
        # finds exactly the same registers -- 0x002A included.
        for x in range(a + n - 1, a - 1, -1):
            todo.insert(0, (x, 1))

    return sorted(set(valid)), dead


def coarse_sweep(bus):
    """Coarse sweep across the entire 16-bit address space.

    Two 8-register samples per 256-address page. Pages already inside
    RANGES are skipped. Finds blocks, not loners -- see the file header.
    """
    covered = set()
    for start, end, _ in RANGES:
        for p in range(start & ~0xFF, end + 1, COARSE_STEP):
            covered.add(p)

    found = []
    pages = [p for p in range(0x0000, 0x10000, COARSE_STEP)
             if p not in covered]
    print("Coarse sweep over %d pages of 0x100, %d samples per page ..."
          % (len(pages), len(COARSE_PROBES)), flush=True)

    for i, page in enumerate(pages, 1):
        for off in COARSE_PROBES:
            a = page + off
            if a + BLOCK - 1 > 0xFFFF:
                continue
            vals, note = bus.read(a, BLOCK)
            if vals is not None:
                print("  HIT on page 0x%04X (at 0x%04X): %s"
                      % (page, a, vals), flush=True)
                found.append(page)
                break
        if i % 40 == 0:
            print("  %d/%d pages" % (i, len(pages)), flush=True)

    if not found:
        print("  No further pages respond.", flush=True)
    return found


LABELS = [
    ("crc", "checksum failed (rtu only)"),
    ("slave", "answer from a different slave address"),
    ("function", "unexpected function code"),
    ("length", "byte count did not match the request"),
    ("foreign_tid", "foreign transaction ID (tcp only)"),
    ("stale_bytes", "leftover bytes discarded before a request"),
    ("timeout", "silence until the timeout expired"),
    ("closed", "gateway closed the connection (another client?)"),
]


def quality_report(bus):
    """Why reads were discarded -- the before/after measure for a gateway
    change. Exceptions are excluded on purpose: they are the device
    answering, not the link failing."""
    lines = ["", "Link quality (%s mode), %d requests:" % (bus.mode, bus.reads)]
    for key, text in LABELS:
        lines.append("  %-12s %6d   %s" % (key, bus.rejects[key], text))
    bad = bus.failed
    lines.append("  %-12s %6d   device answered 'no' (not a fault)"
                 % ("exception", bus.rejects["exception"]))
    rate = (100.0 * bad / bus.reads) if bus.reads else 0.0
    lines.append("  -> %d of %d attempts came back empty (%.2f %%)"
                 % (bad, bus.reads, rate))
    lines.append("     (counted once per attempt; the lines above are the")
    lines.append("      breakdown by reason and can total higher, because one")
    lines.append("      disturbance can trip two of them)")
    if bus.mode == "tcp":
        lines.append("  Note: in tcp mode a stray answer whose transaction ID")
        lines.append("  happens to match is NOT counted here -- it cannot be.")
        lines.append("  That is the reason for --rtu.")
    return "\n".join(lines)


def runs(addrs):
    """Group addresses into contiguous runs."""
    out = []
    for a in addrs:
        if out and a == out[-1][-1] + 1 and len(out[-1]) < BLOCK:
            out[-1].append(a)
        else:
            out.append([a])
    return out


def majority(values):
    """Return the most frequent value, if it occurred at least twice."""
    real = [v for v in values if v is not None]
    if not real:
        return None, False
    best, hits = None, 0
    for v in set(real):
        n = real.count(v)
        if n > hits:
            best, hits = v, n
    return best, hits >= 2


def scan(bus, sweep=False, fast=False):
    samples, notes, area = {}, {}, {}
    all_runs = []
    sweep_hits = []

    todo_ranges = list(RANGES)

    if sweep:
        sweep_hits = coarse_sweep(bus)
        for page in sweep_hits:
            todo_ranges.append(
                (page, page + COARSE_STEP - 1, "sweep 0x%04X" % page))

    for start, end, label in todo_ranges:
        print("Boundary search %s (0x%04X-0x%04X) ..." % (label, start, end),
              flush=True)
        valid, dead = discover(bus, start, end, fast=fast)
        print("  %d of %d addresses respond"
              % (len(valid), end - start + 1), flush=True)
        for a, note in dead.items():
            area[a] = label
            samples.setdefault(a, []).append(None)
            notes.setdefault(a, set()).add(note or "no response")
        for chunk in runs(valid):
            all_runs.append((chunk, label))
            for a in chunk:
                area[a] = label

    print("\nReading %d blocks, %d passes ..."
          % (len(all_runs), PASSES), flush=True)
    for p in range(PASSES):
        for chunk, label in all_runs:
            vals, note = bus.read(chunk[0], len(chunk))
            for k, a in enumerate(chunk):
                samples.setdefault(a, []).append(vals[k] if vals else None)
                if note:
                    notes.setdefault(a, set()).add(note)
        print("  pass %d/%d done" % (p + 1, PASSES), flush=True)

    print("\n%d Modbus requests in total." % bus.reads)
    print(quality_report(bus))

    out = {}
    for a in sorted(samples):
        val, safe = majority(samples[a])
        entry = {
            "address": "0x%04X" % a,
            "area": area.get(a, "?"),
            "value": val,
            "safe": bool(safe),
            "samples": samples[a],
        }
        if a in notes:
            entry["notes"] = sorted(notes[a])
        out["0x%04X" % a] = entry
    return out, sweep_hits


# Read-only function codes worth asking about, with the PDU to send.
# FC8 (diagnostics) is deliberately absent: its sub-functions include
# "restart communications", which is not something to try blindly on an
# inverter wired into a house.
PROBES = [
    (0x01, b"\x01\x00\x00\x00\x08", "read coils 0x0000-0x0007"),
    (0x02, b"\x02\x00\x00\x00\x08", "read discrete inputs 0x0000-0x0007"),
    (0x04, b"\x04\x00\x10\x00\x01", "read input register 0x0010"),
    (0x11, b"\x11", "report server id"),
    (0x2B, b"\x2b\x0e\x01\x00", "read device identification (basic)"),
    (0x2B, b"\x2b\x0e\x02\x00", "read device identification (regular)"),
]

EXCEPTION_TEXT = {
    1: "function not supported",
    2: "illegal address",
    3: "illegal value",
    4: "device failure",
    11: "gateway target failed to respond",
}


def _printable(data):
    """ASCII where it is ASCII, hex where it is not."""
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return "%s   |%s|" % (data.hex(" "), text)


MEI_NAMES = {0: "vendor", 1: "product code", 2: "version",
             3: "vendor url", 4: "product name", 5: "model name",
             6: "application name"}


def decode_mei(data):
    """FC43/14 answer -> {name: text}. The point of asking at all."""
    if len(data) < 7:
        return None
    count = data[6]
    out, pos = {}, 7
    for _ in range(count):
        if pos + 2 > len(data):
            break
        oid, length = data[pos], data[pos + 1]
        value = data[pos + 2:pos + 2 + length]
        out[MEI_NAMES.get(oid, "object %d" % oid)] = \
            value.decode("ascii", "replace")
        pos += 2 + length
    return out or None


def decode_server_id(data):
    """FC17 answer -> the identifier, and whether the device calls itself
    running. The trailing byte is 0xFF for ON, 0x00 for OFF."""
    if len(data) < 2:
        return None
    payload = data[2:2 + data[1]]
    state = None
    if payload and payload[-1] in (0x00, 0xFF):
        state = "running" if payload[-1] == 0xFF else "stopped"
        payload = payload[:-1]
    text = payload.decode("ascii", "replace").strip("\x00").strip()
    out = {"id": text or payload.hex()}
    if state:
        out["state"] = state
    return out


def probe(bus, units=True):
    """Ask what else this device answers to.

    Two questions the register scan cannot reach:

    1. Other function codes. The whole map here was built with FC3 alone.
       FC1 and FC2 address a COMPLETELY SEPARATE space -- that holding
       registers exist says nothing about coils. FC17 and FC43 return
       manufacturer strings and are read-only by definition.

    2. Other unit IDs. Everything assumes slave address 1, but 0x4004 is a
       writable device id according to Marstek's own table, so "1" is an
       assumption, not a measurement.

    Nothing here writes. Unit 0 is the broadcast address and is skipped.
    """
    print("Function codes, unit %d:" % bus.unit, flush=True)
    findings = {"functions": {}, "units": []}
    for code, pdu, what in PROBES:
        data, note = bus.raw(pdu)
        key = "0x%02X %s" % (code, what)
        if data is None:
            print("  %-46s -- %s" % (key, note))
            findings["functions"][key] = {"answer": None, "note": note}
            continue
        if data[0] & 0x80:
            exc = data[1] if len(data) > 1 else 0
            text = EXCEPTION_TEXT.get(exc, "exception %d" % exc)
            print("  %-46s -- exception %d (%s)" % (key, exc, text))
            findings["functions"][key] = {"answer": None, "exception": exc,
                                          "note": text}
            continue
        entry = {"answer": data.hex()}
        decoded = None
        if code == 0x2B:
            decoded = decode_mei(data)
        elif code == 0x11:
            decoded = decode_server_id(data)
        if decoded:
            entry["decoded"] = decoded
            print("  %-46s ->" % key)
            for k, v in decoded.items():
                print("  %46s    %-14s %s" % ("", k + ":", v))
        else:
            print("  %-46s -> %s" % (key, _printable(data)))
        findings["functions"][key] = entry

    if not units:
        return findings

    # The sweep is only worth anything if it finds the one unit that is
    # known to exist. A first run on a real EE11 reported "none" -- unit 1
    # included, which had answered seconds earlier in the same run. The
    # client had given up after 0.5 s while the gateway was still waiting on
    # the bus for the previous, silent request, and every reconnect was
    # refused. So: let the gateway settle, check the known unit before AND
    # after, and refuse to call the result valid otherwise.
    def settle():
        # Wait, THEN reconnect. A socket opened while the gateway was still
        # busy has already been refused and is dead on arrival.
        time.sleep(max(2.0, bus.timeout))
        try:
            bus.connect()
        except OSError:
            pass

    settle()

    def ask(unit):
        data, note = bus.raw(b"\x03\x00\x10\x00\x01", unit=unit)
        if data is None:
            return False, note or "?"
        return True, ("exception" if data[0] & 0x80 else "value")

    control_before, why_before = ask(bus.unit)
    print("\nControl: unit %d %s"
          % (bus.unit, "answers" if control_before
             else "DOES NOT ANSWER (%s)" % why_before), flush=True)

    print("Unit IDs 1-247, one read of 0x0010 each "
          "(this takes a while on silence):", flush=True)
    hits, reasons = [], {}
    for unit in range(1, 248):
        ok, why = ask(unit)
        if ok:
            hits.append(unit)
            print("  unit %3d answers (%s)" % (unit, why), flush=True)
        else:
            kind = ("closed" if "closed" in why or "reset" in why
                    else "timeout" if "timed out" in why or why == "no response"
                    else why)
            reasons[kind] = reasons.get(kind, 0) + 1
        if unit % 50 == 0:
            print("  ... %d/247" % unit, flush=True)
        time.sleep(DELAY)

    settle()
    control_after, why_after = ask(bus.unit)

    control_ok = control_before and control_after and bus.unit in hits
    refused = reasons.get("closed", 0)
    # Three outcomes. The control unit answers: the list is trustworthy.
    # It does not, but others do and the gateway refused nothing: the
    # device simply lives at a different address than --unit says -- which
    # is exactly what this sweep exists to discover. Otherwise: not valid.
    if control_ok:
        valid = True
        verdict = "valid"
    elif hits and refused == 0:
        valid = True
        verdict = "valid, but configured unit silent"
    else:
        valid = False
        verdict = "not valid"

    print("\nNo answer, by reason: %s"
          % (", ".join("%s x%d" % kv for kv in sorted(reasons.items()))
             or "-"))
    if verdict == "valid":
        print("Answering unit IDs: %s" % ", ".join(str(u) for u in hits))
    elif verdict.startswith("valid, but"):
        print("Answering unit IDs: %s" % ", ".join(str(u) for u in hits))
        print("\nNote: the configured unit %d does NOT answer, but the ones "
              "above do." % bus.unit)
        print("The device most likely lives at a different address. Repeat "
              "any scan with")
        print("--unit %d." % hits[0])
    else:
        print("\n*** SWEEP NOT VALID ***")
        print("The control unit %d did not answer reliably (before: %s, "
              "after: %s, in sweep: %s)."
              % (bus.unit, "ok" if control_before else why_before,
                 "ok" if control_after else why_after,
                 "yes" if bus.unit in hits else "no"))
        if refused:
            print("%d probes were refused by the gateway. That is the "
                  "signature of a client" % refused)
            print("timeout shorter than the gateway's own wait: the client "
                  "gives up, reconnects,")
            print("and the gateway -- still busy with the old request -- "
                  "turns it away.")
            print("Repeat with a longer --timeout, e.g. --timeout 2.")
        print("An empty or partial list from this run says nothing about "
              "other units.")

    findings["units"] = hits
    findings["unit_sweep"] = {
        "valid": bool(valid),
        "verdict": verdict,
        "control_unit": bus.unit,
        "control_before": control_before,
        "control_after": control_after,
        "no_answer_reasons": reasons,
        "timeout": bus.timeout,
    }
    return findings


DEAD_ADDRESS = 0x0026   # documented as non-existent on the Jupiter C Plus
# Probes a thorough scan of RANGES (3 x 1024 addresses) needs on a Jupiter C
# Plus, counted against the test device: one per 8-block plus one per
# address of every block that fails. Used only for the runtime estimate.
THOROUGH_PROBES = 3450
LIVE_ADDRESS = 0x0010   # state of charge, always present


def calibrate(host, port, unit, mode):
    """Measure how long the gateway itself waits on the bus.

    Every dead address in a scan costs exactly that long, and a client
    timeout below it makes the gateway refuse the next connection -- the
    failure that made the first unit sweep on 29.09.2026 report "none".
    Three measurements:

      A  how fast a real register answers (lower bound for any timeout)
      B  what a dead request produces: silence, or an exception from the
         gateway after its own wait (exception 11, "target failed to
         respond"), and after how long
      C  how long the gateway stays busy after a request the client gave
         up on: reconnect after d seconds and see whether it lets you in
    """
    live_pdu = struct.pack(">BHH", 3, LIVE_ADDRESS, 1)
    dead_pdu = struct.pack(">BHH", 3, DEAD_ADDRESS, 1)
    result = {"mode": mode, "unit": unit}

    def fresh(timeout):
        return Bus(host, port, unit, timeout, mode=mode)

    # A -- latency of a live register
    print("A  Response time of a live register (0x%04X), 10 reads:"
          % LIVE_ADDRESS, flush=True)
    bus = fresh(5.0)
    times = []
    for _ in range(10):
        t0 = time.time()
        data, note = bus.raw(live_pdu)
        if data is not None and not data[0] & 0x80:
            times.append(time.time() - t0)
        time.sleep(0.2)
    bus.close()
    if not times:
        print("   no answer at all -- check host, unit and that no other "
              "client holds the gateway.")
        result["error"] = "live register did not answer"
        return result
    times.sort()
    lat = {"min": times[0], "median": times[len(times) // 2],
           "max": times[-1], "n": len(times)}
    result["latency"] = lat
    print("   %d/10 answered, min %.0f ms, median %.0f ms, max %.0f ms"
          % (lat["n"], 1000 * lat["min"], 1000 * lat["median"],
             1000 * lat["max"]), flush=True)

    # B -- what does a dead request produce?
    time.sleep(3)
    print("\nB  A request to a dead address (0x%04X), waiting up to 10 s:"
          % DEAD_ADDRESS, flush=True)
    bus = fresh(10.0)
    t0 = time.time()
    data, note = bus.raw(dead_pdu)
    waited = time.time() - t0
    bus.close()
    if data is not None and data[0] & 0x80:
        code = data[1] if len(data) > 1 else 0
        result["dead"] = {"answer": "exception", "code": code,
                          "after": waited}
        print("   exception %d (%s) after %.2f s"
              % (code, EXCEPTION_TEXT.get(code, "?"), waited), flush=True)
    elif data is not None:
        result["dead"] = {"answer": "value", "after": waited}
        print("   a VALUE came back after %.2f s -- 0x%04X is not dead on "
              "this device, or an answer went astray" % (waited, DEAD_ADDRESS))
    else:
        result["dead"] = {"answer": "silence", "after": waited, "note": note}
        print("   silence for %.1f s (%s)" % (waited, note), flush=True)

    # C -- busy window after an abandoned request
    time.sleep(4)
    print("\nC  Gateway busy time: send a dead request, walk away, reconnect "
          "after d seconds:", flush=True)
    delays = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0]
    table = []
    streak = 0
    for d in delays:
        oks = 0
        for _ in range(2):
            try:
                b1 = fresh(5.0)
                b1.send_only(dead_pdu)
                time.sleep(d)
                b1.close()
                b2 = fresh(3.0)
                data, note = b2.raw(live_pdu)
                b2.close()
                if data is not None and not data[0] & 0x80:
                    oks += 1
            except OSError:
                pass
            time.sleep(max(4.0, d + 2.0))    # let everything reset
        table.append({"delay": d, "ok": oks, "of": 2})
        print("   d = %4.2f s   %d/2 let in" % (d, oks), flush=True)
        streak = streak + 1 if oks == 2 else 0
        if streak >= 2:
            break
    result["busy_table"] = table

    # smallest d from which on everything worked
    busy = None
    for i, row in enumerate(table):
        if all(r["ok"] == r["of"] for r in table[i:]):
            busy = row["delay"]
            break
    result["busy_until"] = busy

    # Recommendation
    print("\nResult", flush=True)
    floor = max(lat["max"] * 3, 0.3)
    candidates = [floor]
    if busy is not None:
        candidates.append(busy * 1.5)
    dead = result["dead"]
    if dead["answer"] == "exception":
        candidates.append(dead["after"] * 1.5)
    rec = round(max(candidates) + 0.049, 1)
    result["recommended_timeout"] = rec
    if busy is None:
        print("   The gateway did not let a client back in reliably within "
              "%.0f s. Keep generous timeouts." % delays[-1])
    else:
        print("   Gateway busy after an abandoned request: about %.2f s"
              % busy)
    print("   Recommended --timeout: %.1f s" % rec)
    if dead["answer"] == "exception":
        cost0 = costR = dead["after"]
        print("   A dead address is answered with exception %d after %.2f s."
              % (dead["code"], dead["after"]))
        print("   Exceptions are never retried, so that is its whole cost.")
    else:
        cost0 = rec
        costR = rec * (RETRY + 1)
        print("   A dead address stays silent: it costs the full timeout, "
              "once per attempt.")
        print("   Retries buy nothing here -- silence is the answer. For "
              "mapping, use --retries 0;")
        print("   the three read passes over every register found still "
              "guard the values.")
    result["cost_per_dead_address"] = {"retries_0": cost0,
                                       "retries_default": costR}
    for label_, cost, dly in (("--retries 0 --delay 0.1", cost0, 0.1),
                              ("defaults (--retries %d --delay %.2f)"
                               % (RETRY, DELAY), costR, DELAY)):
        per = cost + dly
        print("   %s:" % label_)
        print("     thorough scan, 3 x 1024 addresses   ~%.0f min"
              % (THOROUGH_PROBES * per / 60))
        print("     every single register, 65536        ~%.1f h"
              % (65536 * per / 3600))
    return result


def benchmark(bus, rounds, label=""):
    """Read the known blocks over and over and report the link quality.

    This is the instrument for a gateway change, not the full scan. Reasons:

    - A dead address answers with silence on this device, so probing it
      costs a full timeout and tells you nothing about the link. Out of a
      full scan, well over 90 % of the time is spent waiting for registers
      that were never there.
    - It mirrors what a client actually does in daily use: the same handful
      of blocks, over and over.
    - It takes minutes, so before and after can be measured on the same day,
      under comparable conditions.

    Values are checked for consistency as well: a register that changes
    between rounds is normal for a measurement, but the run records how
    often each block answered at all.
    """
    print("Benchmark: %d rounds over %d known blocks (%d registers), "
          "%s mode" % (rounds, len(KNOWN_BLOCKS),
                       sum(n for _, n in KNOWN_BLOCKS), bus.mode), flush=True)
    per_block = {}
    started = time.time()
    for r in range(rounds):
        for addr, count in KNOWN_BLOCKS:
            key = "0x%04X+%d" % (addr, count)
            rec = per_block.setdefault(key, {"ok": 0, "fail": 0, "notes": {}})
            vals, note = bus.read(addr, count)
            if vals is None:
                rec["fail"] += 1
                rec["notes"][note or "?"] = rec["notes"].get(note or "?", 0) + 1
            else:
                rec["ok"] += 1
        if (r + 1) % 5 == 0 or r + 1 == rounds:
            print("  round %d/%d, %d requests, %.0f s elapsed"
                  % (r + 1, rounds, bus.reads, time.time() - started),
                  flush=True)

    print("\nPer block:")
    print("  %-14s %6s %6s   %s" % ("block", "ok", "fail", "reasons"))
    for key in sorted(per_block):
        rec = per_block[key]
        reasons = ", ".join("%s x%d" % (k, v)
                            for k, v in sorted(rec["notes"].items()))
        print("  %-14s %6d %6d   %s" % (key, rec["ok"], rec["fail"], reasons))
    print(quality_report(bus))
    print("\nDuration: %.0f s" % (time.time() - started))

    # Write it down. A benchmark that only prints to a terminal cannot be
    # held against the next one, which is the whole point of measuring.
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    name = "benchmark_%s_%s.json" % (label, stamp) if label \
        else "benchmark_%s.json" % stamp
    path = os.path.join(OUTDIR, name)
    with open(path, "w") as fh:
        json.dump({"created": datetime.now().isoformat(timespec="seconds"),
                   "label": label,
                   "mode": bus.mode,
                   "rounds": rounds,
                   "requests": bus.reads,
                   "rejects": dict(bus.rejects),
                   "failed_attempts": bus.failed,
                   "seconds": round(time.time() - started, 1),
                   "blocks": per_block}, fh, indent=1)
    print("written:\n  %s" % path)
    return per_block


def write_report(data, label, host, unit, sweep_hits=None, swept=False,
                 bus=None):
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    base = "regdump_%s_%s" % (label, stamp) if label else "regdump_%s" % stamp
    jpath = os.path.join(OUTDIR, base + ".json")
    tpath = os.path.join(OUTDIR, base + ".txt")

    meta = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "host": host,
        "unit": unit,
        "passes": PASSES,
        "sweep": bool(swept),
        "sweep_hits": ["0x%04X" % p for p in (sweep_hits or [])],
        "registers": data,
    }
    if bus is not None:
        meta["mode"] = bus.mode
        meta["requests"] = bus.reads
        meta["rejects"] = dict(bus.rejects)
        meta["failed_attempts"] = bus.failed
    with open(jpath, "w") as fh:
        json.dump(meta, fh, indent=1, ensure_ascii=False)

    live = {k: v for k, v in data.items() if v["value"] is not None}
    lines = ["Register dump Jupiter C Plus",
             "created: %s" % meta["created"],
             "label:   %s" % (label or "-"),
             "mode:    %s" % (bus.mode if bus else "tcp")]
    if bus is not None:
        lines += quality_report(bus).splitlines()
    lines.append("")
    if swept:
        if sweep_hits:
            lines.append("Coarse sweep over 0x0000-0xFFFF: hits on %s"
                         % ", ".join("0x%04X" % p for p in sweep_hits))
        else:
            lines.append("Coarse sweep over 0x0000-0xFFFF: no further "
                         "pages respond.")
        lines.append("(Finds blocks, not isolated single registers.)")
        lines.append("")
    lines += ["%d addresses hold a value. Empty addresses are only"
              % len(live),
              "summarised at the bottom.",
              "",
              "Address  Value   Safe    Area / note",
              "-" * 64]
    for key in sorted(live, key=lambda k: int(k, 16)):
        e = live[key]
        extra = e["area"]
        if e.get("notes"):
            extra += " | " + ", ".join(e["notes"])
        lines.append("%-8s %-7s %-7s %s"
                     % (key, e["value"], "yes" if e["safe"] else "NO",
                        extra))

    empty = sorted((k for k in data if data[k]["value"] is None),
                   key=lambda k: int(k, 16))
    lines += ["", "Without content (%d addresses):" % len(empty)]
    block = []
    for k in empty + [None]:
        n = int(k, 16) if k else None
        if block and n == block[-1] + 1:
            block.append(n)
            continue
        if block:
            lines.append("  0x%04X - 0x%04X  (%d)"
                         % (block[0], block[-1], len(block))
                         if len(block) > 1 else "  0x%04X" % block[0])
        block = [n] if n is not None else []
    with open(tpath, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print("written:\n  %s\n  %s" % (jpath, tpath))
    return jpath


def load(path):
    with open(path) as fh:
        blob = json.load(fh)
    return blob.get("registers") or blob["register"]


def load_meta(path):
    """The run's own metadata -- mode and reject counters, absent in dumps
    written before those existed."""
    with open(path) as fh:
        blob = json.load(fh)
    if not isinstance(blob, dict) or "rejects" not in blob:
        return None
    return {"mode": blob.get("mode", "?"),
            "requests": blob.get("requests", 0),
            "rejects": blob["rejects"],
            # Dumps from before that counter existed: fall back to the sum
            # of the reasons and say so, because one disturbance can trip
            # two of them and the sum is therefore an upper bound.
            "failed": blob.get("failed_attempts"),
            "failed_exact": "failed_attempts" in blob,
            "label": blob.get("label") or os.path.basename(path)}


def quality_diff(a, b):
    """Two runs side by side: did the gateway change help?

    Exceptions are left out -- they are the device answering, not the link
    failing, and their number depends on how much empty space was scanned.
    """
    if not (a and b):
        print("\n(No link-quality comparison: at least one dump predates "
              "the counters.)")
        return
    print("\n==== Link quality ====")
    print("  %-14s %22s %22s" % ("", a["label"], b["label"]))
    print("  %-14s %22s %22s"
          % ("mode", a["mode"], b["mode"]))
    print("  %-14s %22d %22d" % ("requests", a["requests"], b["requests"]))
    for key, text in LABELS:
        print("  %-14s %22d %22d   %s"
              % (key, a["rejects"].get(key, 0), b["rejects"].get(key, 0), text))
    def empties(run):
        if run.get("failed_exact"):
            return run["failed"], ""
        total = sum(v for k, v in run["rejects"].items()
                    if k not in ("exception", "stale_bytes"))
        return total, "~"
    fa, ma = empties(a)
    fb, mb = empties(b)
    ra = 100.0 * fa / a["requests"] if a["requests"] else 0.0
    rb = 100.0 * fb / b["requests"] if b["requests"] else 0.0
    print("  %-14s %21s%d %21s%d   attempts that came back empty"
          % ("empty", ma, fa, mb, fb))
    print("  %-14s %21.2f%% %21.2f%%" % ("", ra, rb))
    if ma or mb:
        print("\n  ~ = older dump without the per-attempt counter. The value")
        print("  is the sum of the reasons, which is an upper bound: in tcp")
        print("  mode one disturbance trips both foreign_tid and timeout.")
    if a["mode"] != b["mode"]:
        print("\n  Different modes, so read the numbers with care: the tcp")
        print("  figure is a lower bound. A stale answer whose transaction")
        print("  ID happens to match is counted nowhere -- that is the")
        print("  failure --rtu exists to expose.")


def _value(entry):
    if entry is None:
        return None
    return entry.get("value", entry.get("wert"))


def diff(old, new):
    keys = sorted(set(old) | set(new), key=lambda k: int(k, 16))
    changed, gone, added = [], [], []
    for k in keys:
        ov = _value(old.get(k))
        nv = _value(new.get(k))
        if ov is None and nv is not None:
            added.append((k, nv))
        elif ov is not None and nv is None:
            gone.append((k, ov))
        elif ov != nv:
            changed.append((k, ov, nv))

    print("\n==== Comparison ====")
    if added:
        print("\nNEWLY readable -- something was added here:")
        for k, v in added:
            print("  %s  ->  %s" % (k, v))
    if gone:
        print("\nGONE -- no longer responds:")
        for k, v in gone:
            print("  %s  was %s" % (k, v))
    if changed:
        print("\nVALUE CHANGED. Normal for measurements. Pay attention to")
        print("registers that ought to be constant -- versions")
        print("(0x001B-0x001F, 0x0022) and device type (0x0025):")
        for k, o, n in changed:
            print("  %s  %s  ->  %s" % (k, o, n))
    if not (added or gone or changed):
        print("\nNo differences.")
    print()


def main():
    global DELAY, TIMEOUT, RETRY
    ap = argparse.ArgumentParser(
        description="Register dump for the Marstek Jupiter C Plus")
    ap.add_argument("--host", default=HOST,
                    help="Modbus TCP gateway (default: %s)" % HOST)
    ap.add_argument("--port", type=int, default=PORT,
                    help="TCP port (default: %d)" % PORT)
    ap.add_argument("--unit", type=int, default=UNIT,
                    help="Modbus slave address (default: %d)" % UNIT)
    ap.add_argument("--label", default="", help="name for the output file")
    ap.add_argument("--rtu", action="store_true",
                    help="speak Modbus RTU over TCP instead of Modbus TCP. "
                         "Requires the gateway's protocol to be set to "
                         "'None'/transparent. Frames then carry a CRC16 and "
                         "the socket is drained before each request, so "
                         "merged, split and misaligned answers fail loudly "
                         "instead of being guessed at")
    ap.add_argument("--sweep", action="store_true",
                    help="also sweep 0x0000-0xFFFF coarsely")
    ap.add_argument("--delay", type=float, default=DELAY,
                    help="seconds between two requests (default: %.2f). "
                         "Deliberately slow so the bus carries only a little "
                         "extra load next to your normal polling. Lower it "
                         "and watch the link quality tally at the end -- "
                         "that is how you find your gateway's real limit"
                         % DELAY)
    ap.add_argument("--calibrate", action="store_true",
                    help="do not scan: measure how fast a live register "
                         "answers, what a dead request produces, and how "
                         "long the gateway stays busy afterwards -- then "
                         "recommend a --timeout and estimate scan times")
    ap.add_argument("--probe", action="store_true",
                    help="do not scan: ask the device which other function "
                         "codes it answers (coils, discrete inputs, report "
                         "server id, device identification) and which unit "
                         "IDs respond. Read-only throughout; FC8 is left "
                         "out on purpose")
    ap.add_argument("--no-units", action="store_true",
                    help="with --probe: skip the unit-ID sweep, which is the "
                         "slow half")
    ap.add_argument("--benchmark", type=int, metavar="ROUNDS", nargs="?",
                    const=20,
                    help="do not scan: read the known blocks ROUNDS times "
                         "(default 20) and report the link quality. This is "
                         "the measurement for a gateway change -- minutes "
                         "instead of hours, because it does not wait on "
                         "addresses that were never there")
    ap.add_argument("--timeout", type=float, default=TIMEOUT,
                    help="seconds to wait for an answer (default: %.1f). On "
                         "this device a non-existent address does not answer "
                         "at all, so the timeout is what a full scan spends "
                         "most of its time on" % TIMEOUT)
    ap.add_argument("--retries", type=int, default=RETRY,
                    help="retries after a timeout (default: %d). Exceptions "
                         "are never retried -- they are the device speaking"
                         % RETRY)
    ap.add_argument("--fast", action="store_true",
                    help="skip empty 8-register blocks after probing both "
                         "ends instead of probing every register. "
                         "Roughly three times quicker and loses isolated "
                         "registers such as 0x002A -- only for a rough "
                         "boundary check, never for a dump you intend to "
                         "diff against")
    ap.add_argument("--diff", metavar="OLD.JSON",
                    help="hold a fresh dump against this file")
    ap.add_argument("--diff-only", metavar="NEW.JSON",
                    help="only compare two existing files")
    args = ap.parse_args()

    if args.diff_only:
        if not args.diff:
            ap.error("--diff-only also needs --diff <old file>")
        diff(load(args.diff), load(args.diff_only))
        quality_diff(load_meta(args.diff), load_meta(args.diff_only))
        return

    DELAY = args.delay
    TIMEOUT = args.timeout
    RETRY = args.retries

    if args.calibrate:
        res = calibrate(args.host, args.port, args.unit,
                        "rtu" if args.rtu else "tcp")
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        name = "calibrate_%s_%s.json" % (args.label, stamp) if args.label \
            else "calibrate_%s.json" % stamp
        path = os.path.join(OUTDIR, name)
        with open(path, "w") as fh:
            json.dump(dict(res, created=datetime.now().isoformat(
                timespec="seconds"), host=args.host), fh, indent=1)
        print("written:\n  %s" % path)
        return 0

    try:
        bus = Bus(args.host, args.port, args.unit, TIMEOUT,
                  mode="rtu" if args.rtu else "tcp")
    except OSError as exc:
        print("No connection to %s:%d -- %s" % (args.host, args.port, exc))
        print("Check the address, and that the gateway is reachable from")
        print("this machine. Nothing was read, nothing was written.")
        return 1
    if args.probe:
        try:
            found = probe(bus, units=not args.no_units)
        finally:
            bus.close()
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        name = "probe_%s_%s.json" % (args.label, stamp) if args.label \
            else "probe_%s.json" % stamp
        path = os.path.join(OUTDIR, name)
        with open(path, "w") as fh:
            json.dump({"created": datetime.now().isoformat(timespec="seconds"),
                       "host": args.host, "mode": bus.mode,
                       "unit": args.unit, "findings": found}, fh, indent=1)
        print("written:\n  %s" % path)
        return 0

    if args.benchmark:
        try:
            benchmark(bus, args.benchmark, args.label)
        finally:
            bus.close()
        return 0

    try:
        data, hits = scan(bus, sweep=args.sweep, fast=args.fast)
    finally:
        bus.close()

    used = sum(1 for e in data.values() if e["value"] is not None)
    safe = sum(1 for e in data.values() if e["safe"])
    print("\n%d addresses checked, %d hold a value, %d of those unambiguous."
          % (len(data), used, safe))
    if used != safe:
        print("The unsafe ones are marked 'NO' in the text file -- that is")
        print("where the gateway interfered. Repeat the run if needed.")

    write_report(data, args.label, args.host, args.unit, hits, args.sweep,
                 bus=bus)

    if args.diff:
        diff(load(args.diff), data)
        quality_diff(load_meta(args.diff),
                     {"mode": bus.mode, "requests": bus.reads,
                      "rejects": bus.rejects, "failed": bus.failed,
                      "label": args.label or "this run"})


if __name__ == "__main__":
    sys.exit(main())
