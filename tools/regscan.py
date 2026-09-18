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
   Hence the two-phase scan: find the boundaries first (halving blocks on
   failure, down to single registers), then read the discovered ranges.

3. The RS485-to-Ethernet gateway handles one request at a time and will
   pass foreign responses through when the transaction ID matches. While
   another client polls the same device in parallel, single measurements
   cannot be trusted.
   Countermeasure: every value is read several times and only a majority
   verdict is accepted. Anything else is flagged as unsafe rather than
   silently used.

On the coarse sweep (--sweep)
-----------------------------
The thorough scan only covers RANGES. --sweep additionally probes the whole
16-bit address space: two 8-register samples per 256-address page. If either
answers, the whole page is then scanned thoroughly.

HONEST LIMITATION of that method: it finds blocks, not loners. A single
valid register between dead neighbours -- such as 0x002A -- makes every
8-register probe fail and stays invisible. That is exactly how the first
full scan missed 0x002A. To find isolated registers, put the range into
RANGES, where blocks get halved.
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

# Ranges scanned thoroughly -- blocks are halved on failure here, so
# isolated single registers are found too. Widened from 0x100 to 0x400
# each after 0x002A showed that Marstek also places registers outside the
# known blocks.
RANGES = [
    (0x0000, 0x03FF, "data block"),
    (0x1000, 0x13FF, "status flags"),
    (0x4000, 0x43FF, "write registers (usually empty when read)"),
]

# Coarse sweep: page size and sample offsets within the page.
COARSE_STEP = 0x0100
COARSE_PROBES = (0x00, 0x80)

OUTDIR = os.path.dirname(os.path.abspath(__file__))


class Bus:
    """Modbus TCP, deliberately without a library.

    Raw sockets, so that the transaction ID and length can be validated
    here -- precisely the two things cheap gateways get wrong.
    """

    def __init__(self, host, port, unit, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.unit = unit
        self.sock = None
        self.tid = 0
        self.reads = 0
        self.connect()

    def connect(self):
        self.close()
        self.sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)

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
                self.connect()
                return None, "no response: %s" % exc

            rtid, pid, length = struct.unpack(">HHH", head)
            if length < 2 or length > 260:
                self.connect()
                return None, "implausible length %d" % length

            try:
                body = self._recv_exact(length)
            except (OSError, ConnectionError) as exc:
                self.connect()
                return None, "response truncated: %s" % exc

            if rtid != tid or pid != 0 or body[0] != self.unit:
                continue                      # foreign response, keep waiting

            func = body[1]
            if func == 0x83:
                return None, "exception %d" % body[2]
            if func != 3:
                return None, "unexpected function code %d" % func

            nbytes = body[2]
            payload = body[3:3 + nbytes]
            if nbytes != count * 2 or len(payload) != nbytes:
                return None, "length mismatch (%d instead of %d)" % (
                    nbytes, count * 2)

            return list(struct.unpack(">%dH" % count, payload)), None

        return None, "only foreign responses received"

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
        return None, note


def discover(bus, start, end):
    """Find the actual boundaries of valid ranges.

    A block reaching even partially into nothing fails entirely. So: halve
    on failure and check both halves separately, down to single registers.

    Shortcut for large empty zones: if an 8-register block reports
    exception 2 (illegal address) and its first and last register report
    exception 2 individually as well, the whole block counts as empty.
    That saves walking through hundreds of non-existent addresses.
    """
    valid, dead = [], {}
    todo = []
    addr = start
    while addr <= end:
        todo.append((addr, min(BLOCK, end - addr + 1)))
        addr += BLOCK

    while todo:
        a, n = todo.pop(0)
        vals, note = bus.read(a, n)
        if vals is not None:
            valid.extend(range(a, a + n))
            continue
        if n == 1:
            dead[a] = note
            continue
        if note == "exception 2" and n == BLOCK:
            v1, n1 = bus.read(a, 1)
            v2, n2 = bus.read(a + n - 1, 1)
            if v1 is None and n1 == "exception 2" and \
               v2 is None and n2 == "exception 2":
                for x in range(a, a + n):
                    dead[x] = "exception 2"
                continue
            if v1 is not None:
                valid.append(a)
            if v2 is not None:
                valid.append(a + n - 1)
            if n > 2:
                todo.insert(0, (a + 1, n - 2))
            continue
        half = n // 2
        todo.insert(0, (a + half, n - half))
        todo.insert(0, (a, half))

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


def scan(bus, sweep=False):
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
        valid, dead = discover(bus, start, end)
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


def write_report(data, label, host, unit, sweep_hits=None, swept=False):
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
    with open(jpath, "w") as fh:
        json.dump(meta, fh, indent=1, ensure_ascii=False)

    live = {k: v for k, v in data.items() if v["value"] is not None}
    lines = ["Register dump Jupiter C Plus",
             "created: %s" % meta["created"],
             "label:   %s" % (label or "-"),
             ""]
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
    ap = argparse.ArgumentParser(
        description="Register dump for the Marstek Jupiter C Plus")
    ap.add_argument("--host", default=HOST,
                    help="Modbus TCP gateway (default: %s)" % HOST)
    ap.add_argument("--port", type=int, default=PORT,
                    help="TCP port (default: %d)" % PORT)
    ap.add_argument("--unit", type=int, default=UNIT,
                    help="Modbus slave address (default: %d)" % UNIT)
    ap.add_argument("--label", default="", help="name for the output file")
    ap.add_argument("--sweep", action="store_true",
                    help="also sweep 0x0000-0xFFFF coarsely")
    ap.add_argument("--diff", metavar="OLD.JSON",
                    help="hold a fresh dump against this file")
    ap.add_argument("--diff-only", metavar="NEW.JSON",
                    help="only compare two existing files")
    args = ap.parse_args()

    if args.diff_only:
        if not args.diff:
            ap.error("--diff-only also needs --diff <old file>")
        diff(load(args.diff), load(args.diff_only))
        return

    bus = Bus(args.host, args.port, args.unit, TIMEOUT)
    try:
        data, hits = scan(bus, sweep=args.sweep)
    finally:
        bus.close()

    used = sum(1 for e in data.values() if e["value"] is not None)
    safe = sum(1 for e in data.values() if e["safe"])
    print("\n%d addresses checked, %d hold a value, %d of those unambiguous."
          % (len(data), used, safe))
    if used != safe:
        print("The unsafe ones are marked 'NO' in the text file -- that is")
        print("where the gateway interfered. Repeat the run if needed.")

    write_report(data, args.label, args.host, args.unit, hits, args.sweep)

    if args.diff:
        diff(load(args.diff), data)


if __name__ == "__main__":
    sys.exit(main())
