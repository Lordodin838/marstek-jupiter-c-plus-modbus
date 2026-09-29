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


SERVER_ID = b"\x0aJupiterC+\xff"      # Laenge, Kennung, Run-Indicator

# FC43/14: Hersteller, Produkt, Version -- so wie ein Geraet es liefern wuerde,
# das Read Device Identification beherrscht.
MEI_OBJECTS = {0: b"Marstek", 1: b"Jupiter C Plus", 2: b"142.37.213.110"}


def handle(dev, pdu):
    """Ein PDU rein, ein PDU raus. None heisst: gar nicht antworten."""
    if not pdu:
        return None
    func = pdu[0]

    if func == 3:
        if len(pdu) < 5:
            return None
        addr, count = struct.unpack(">HH", pdu[1:5])
        exc, vals = dev.answer(addr, count)
        if exc is SCHWEIGEN:
            return None
        if exc:
            return struct.pack(">BB", func | 0x80, exc)
        return struct.pack(">BB", func, count * 2) + \
            struct.pack(">%dH" % count, *vals)

    if func == 0x11:                      # Report Server ID
        return bytes([func, len(SERVER_ID)]) + SERVER_ID

    if func == 0x2B and len(pdu) >= 4 and pdu[1] == 0x0E:
        # Read Device Identification, nur Basisdaten
        body = bytes([0x2B, 0x0E, pdu[2], 0x01, 0x00, 0x00, len(MEI_OBJECTS)])
        for oid in sorted(MEI_OBJECTS):
            val = MEI_OBJECTS[oid]
            body += bytes([oid, len(val)]) + val
        return body

    # Alles andere kennt das Geraet nicht -- genau wie FC4 am echten Jupiter.
    return bytes([func | 0x80, 1])


def serve_rtu(conn, dev):
    buf = b""
    while True:
        chunk = conn.recv(512)
        if not chunk:
            return
        buf += chunk
        while len(buf) >= 4:
            if crc16(buf[:-2]) != buf[-2:]:
                break                         # noch unvollstaendig
            frame, buf = buf, b""
            if frame[0] != dev.unit:
                break                         # nicht fuer uns: schweigen
            answer = handle(dev, frame[1:-2])
            if answer is None:
                break
            body = bytes([dev.unit]) + answer
            out = body + crc16(body)
            if dev.should_corrupt():
                out = out[:-1] + bytes([out[-1] ^ 0xFF])
            conn.sendall(out)


def serve_tcp(conn, dev):
    buf = b""
    while True:
        chunk = conn.recv(512)
        if not chunk:
            return
        buf += chunk
        while len(buf) >= 8:
            tid, pid, ln = struct.unpack(">HHH", buf[:6])
            if len(buf) < 6 + ln:
                break
            unit = buf[6]
            pdu, buf = buf[7:6 + ln], buf[6 + ln:]
            if unit != dev.unit:
                continue                      # schweigen, wie am Bus
            answer = handle(dev, pdu)
            if answer is None:
                continue
            body = bytes([unit]) + answer
            if dev.should_corrupt():
                tid = (tid + 1) & 0xFFFF
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
