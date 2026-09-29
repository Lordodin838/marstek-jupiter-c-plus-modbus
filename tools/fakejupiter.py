#!/usr/bin/env python3
"""Testgeraet: spielt einen Jupiter C Plus hinter einem Elfin.

Kennt beide Betriebsarten (Modbus TCP und RTU over TCP) und bildet die drei
Eigenheiten nach, auf die regscan.py ausgelegt ist:
  - hoechstens 8 Register je Anfrage, darueber Exception 3
  - eine Anfrage, die auch nur teilweise ins Leere reicht, scheitert ganz
  - nur FC3

Mit --corrupt N faellt jede N-te Antwort absichtlich kaputt aus: im RTU-Modus
mit falscher Pruefsumme, im TCP-Modus mit fremder Transaktions-ID. Damit
laesst sich pruefen, ob der Scanner das auch merkt.

WICHTIG, --silent:
    Das echte Geraet antwortet auf eine nicht vorhandene Adresse GAR NICHT,
    es liefert keine Exception 2. Genau daran haengt die Laufzeit eines
    vollstaendigen Scans: jede tote Adresse kostet einen Timeout statt einer
    sofortigen Absage. Ein Testgeraet, das brav mit Exception antwortet, ist
    in Sekunden durch und verleitet zu Laufzeitschaetzungen, die um den
    Faktor 20 danebenliegen. Genau das ist am 29.09.2026 passiert.

    --silent bildet das echte Verhalten ab und ist deshalb die Voreinstellung
    fuer alles, was mit Laufzeit zu tun hat. --exceptions erzwingt die
    schnelle Variante, wenn man nur Logik pruefen will.
"""
import argparse
import socket
import struct
import threading

# Belegte Bereiche wie am echten Geraet
VALID = set(range(0x0001, 0x0026)) | {0x002A} | set(range(0x1000, 0x100B)) \
    | set(range(0x1100, 0x1106)) | set(range(0x1200, 0x1206))


def crc16(frame):
    crc = 0xFFFF
    for b in frame:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def value(addr):
    return (addr * 7) & 0xFFFF


SCHWEIGEN = object()   # Rueckgabewert: gar nicht antworten


class Device:
    def __init__(self, unit, corrupt, silent=True):
        self.unit = unit
        self.corrupt = corrupt
        self.silent = silent
        self.n = 0

    def answer(self, addr, count):
        """(exception_code, values) -- genau eines ist gesetzt.

        Bei einer toten Adresse ist das Ergebnis (SCHWEIGEN, None), wenn
        silent gesetzt ist: der Aufrufer sendet dann nichts und der Client
        laeuft in seinen Timeout, wie am echten Geraet.
        """
        if count < 1 or count > 8:
            return 3, None
        if any(a not in VALID for a in range(addr, addr + count)):
            return (SCHWEIGEN if self.silent else 2), None
        return None, [value(a) for a in range(addr, addr + count)]

    def should_corrupt(self):
        if not self.corrupt:
            return False
        self.n += 1
        return self.n % self.corrupt == 0


def serve_rtu(conn, dev):
    buf = b""
    while True:
        chunk = conn.recv(256)
        if not chunk:
            return
        buf += chunk
        while len(buf) >= 8:
            req, buf = buf[:8], buf[8:]
            if crc16(req[:6]) != req[6:8]:
                continue                      # stilles Verwerfen, wie am Bus
            slave, func, addr, count = struct.unpack(">BBHH", req[:6])
            if slave != dev.unit or func != 3:
                continue
            exc, vals = dev.answer(addr, count)
            if exc is SCHWEIGEN:
                continue                      # gar nicht antworten
            if exc:
                body = struct.pack(">BBB", slave, func | 0x80, exc)
            else:
                body = struct.pack(">BBB", slave, func, count * 2)
                body += struct.pack(">%dH" % count, *vals)
            frame = body + crc16(body)
            if dev.should_corrupt():
                frame = frame[:-1] + bytes([frame[-1] ^ 0xFF])
            conn.sendall(frame)


def serve_tcp(conn, dev):
    buf = b""
    while True:
        chunk = conn.recv(256)
        if not chunk:
            return
        buf += chunk
        while len(buf) >= 12:
            req, buf = buf[:12], buf[12:]
            tid, pid, ln, slave, func, addr, count = struct.unpack(
                ">HHHBBHH", req)
            if slave != dev.unit or func != 3:
                continue
            exc, vals = dev.answer(addr, count)
            if exc is SCHWEIGEN:
                continue                      # gar nicht antworten
            if exc:
                body = struct.pack(">BBB", slave, func | 0x80, exc)
            else:
                body = struct.pack(">BBB", slave, func, count * 2)
                body += struct.pack(">%dH" % count, *vals)
            if dev.should_corrupt():
                tid = (tid + 1) & 0xFFFF      # fremde Antwort vortaeuschen
            conn.sendall(struct.pack(">HHH", tid, 0, len(body)) + body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5502)
    ap.add_argument("--unit", type=int, default=1)
    ap.add_argument("--rtu", action="store_true")
    ap.add_argument("--corrupt", type=int, default=0,
                    help="jede N-te Antwort kaputt machen")
    ap.add_argument("--exceptions", action="store_true",
                    help="tote Adressen mit Exception 2 beantworten statt zu "
                         "schweigen. Schnell, aber NICHT das Verhalten des "
                         "echten Geraets - nur fuer Logikpruefungen")
    args = ap.parse_args()

    dev = Device(args.unit, args.corrupt, silent=not args.exceptions)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(5)
    print("Testgeraet auf 127.0.0.1:%d (%s, tote Adressen: %s)"
          % (args.port, "rtu" if args.rtu else "tcp",
             "Exception 2" if args.exceptions else "Schweigen"), flush=True)
    handler = serve_rtu if args.rtu else serve_tcp
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handler, args=(conn, dev),
                         daemon=True).start()


if __name__ == "__main__":
    main()
