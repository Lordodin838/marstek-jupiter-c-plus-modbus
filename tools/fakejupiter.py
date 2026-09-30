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

Tote Adressen, wie am echten Geraet gemessen (29.09.2026, Unit 1 und 11):
    Fast jede nicht vorhandene Adresse wird sofort mit Exception 2
    abgelehnt, in rund 0,2 s. Die vier Adressen 0x0026-0x0029 sind
    dagegen echte, aber LANGSAME Register: das Geraet antwortet erst nach
    ~3,6 s. Ein EE11 auf "Auto" gibt vorher auf - der Client bekommt gar
    nichts, und das Geraet braucht danach 2-3 s, bis es wieder annimmt
    (mit --busy nachbilden). Das ist die Voreinstellung, weil es das ist,
    was ein Client hinter einem Gateway mit Standardeinstellung sieht.
    --gap-delay 3.6 bildet stattdessen einen EE11 mit festen 5000 ms nach.

    Eine fruehere Fassung liess JEDE tote Adresse schweigen, weil vom
    echten Geraet nur die vermeintliche Luecke bekannt war. Das hat Laufzeitschaetzungen
    um mehr als den Faktor zehn aufgeblaeht. --all-silent gibt es fuer den
    Vergleich weiterhin, bildet aber nicht den Jupiter ab.

    --exceptions laesst auch die Luecke mit Exception 2 antworten - fuer
    reine Logikpruefungen ohne Wartezeiten.
"""
import argparse
import socket
import struct
import threading
import time

# Belegte Bereiche wie am echten Geraet
VALID = set(range(0x0001, 0x0026)) | {0x002A} | set(range(0x1000, 0x100B)) \
    | set(range(0x1100, 0x1106)) | set(range(0x1200, 0x1206))

# Adressen, die am echten Geraet erst nach ~3,6 s antworten. Mit "Auto" gibt
# der EE11 vorher auf, dann sieht es nach Stille aus (29.09.2026 gemessen).
GAP = set(range(0x0026, 0x002A))
GAP_VALUES = {0x0026: 1, 0x0027: 0, 0x0028: 1, 0x0029: 0}


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
    def __init__(self, unit, corrupt, dead="jupiter", busy=0.0):
        self.unit = unit
        self.corrupt = corrupt
        self.dead = dead           # "jupiter", "exceptions" oder "silent"
        self.n = 0
        # Nachbildung eines Gateways, das nach einer unbeantworteten Anfrage
        # selbst noch eine Weile auf den RS485-Bus wartet ("Modbus TimeOut:
        # auto") und in dieser Zeit keinen neuen Client annimmt.
        self.busy = busy
        self.busy_until = 0.0
        self.no_ident = False
        self.exc11 = None     # Sekunden bis zur Gateway-Ausnahme, sonst Stille
        self.gap_delay = None  # Luecke antwortet nach so vielen Sekunden
        self.last_slow = False

    def mark_silence(self):
        if self.busy:
            self.busy_until = time.time() + self.busy

    def is_busy(self):
        return time.time() < self.busy_until

    def answer(self, addr, count):
        """(exception_code, values) -- genau eines ist gesetzt.

        Bei einer toten Adresse ist das Ergebnis (SCHWEIGEN, None), wenn das
        Geraet dort schweigt: der Aufrufer sendet dann nichts und der Client
        laeuft in seinen Timeout. Welche Adressen schweigen, bestimmt
        self.dead - siehe Dateikopf.
        """
        if count < 1 or count > 8:
            return 3, None
        wanted = range(addr, addr + count)
        self.last_slow = False
        if any(a not in VALID for a in wanted):
            touches_gap = any(a in GAP for a in wanted)
            only_known = all(a in VALID or a in GAP for a in wanted)
            if self.dead == "jupiter" and touches_gap and only_known:
                if self.gap_delay is None:
                    return SCHWEIGEN, None      # Gateway gibt vorher auf
                self.last_slow = True
                return None, [GAP_VALUES.get(a, value(a)) for a in wanted]
            if self.dead == "silent":
                return SCHWEIGEN, None
            if self.dead == "jupiter" and touches_gap:
                return SCHWEIGEN, None
            return 2, None
        return None, [value(a) for a in wanted]

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

    if func in (0x11, 0x2B) and dev.no_ident:
        return None                       # wie hinter dem EE11 beobachtet

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
            if dev.is_busy():
                break
            if frame[0] != dev.unit:
                dev.mark_silence()
                break                         # nicht fuer uns: schweigen
            answer = handle(dev, frame[1:-2])
            if answer is None:
                dev.mark_silence()
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
            if dev.is_busy():
                continue                      # Gateway haengt noch
            answer = handle(dev, pdu) if unit == dev.unit else None
            if answer is None:
                dev.mark_silence()
                if dev.exc11 is not None:
                    # Gateway gibt nach seiner eigenen Wartezeit auf und
                    # meldet das: Exception 11, "target failed to respond".
                    func = pdu[0] if pdu else 3
                    body = bytes([unit, func | 0x80, 0x0B])
                    frame = struct.pack(">HHH", tid, 0, len(body)) + body

                    def spaeter(c=conn, f=frame):
                        try:
                            c.sendall(f)
                        except OSError:
                            pass
                    threading.Timer(dev.exc11, spaeter).start()
                continue
            body = bytes([unit]) + answer
            if dev.should_corrupt():
                tid = (tid + 1) & 0xFFFF
            frame = struct.pack(">HHH", tid, 0, len(body)) + body
            if dev.last_slow:
                # langsame Luecke: spaet antworten, bis dahin besetzt
                dev.busy_until = time.time() + dev.gap_delay

                def spaet(c=conn, f=frame):
                    try:
                        c.sendall(f)
                    except OSError:
                        pass
                threading.Timer(dev.gap_delay, spaet).start()
                continue
            conn.sendall(frame)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5502)
    ap.add_argument("--unit", type=int, default=1)
    ap.add_argument("--rtu", action="store_true")
    ap.add_argument("--corrupt", type=int, default=0,
                    help="jede N-te Antwort kaputt machen")
    ap.add_argument("--all-silent", action="store_true",
                    help="JEDE tote Adresse schweigen lassen - das alte, "
                         "falsche Modell, nur noch zum Vergleich")
    ap.add_argument("--exceptions", action="store_true",
                    help="tote Adressen mit Exception 2 beantworten statt zu "
                         "schweigen. Schnell, aber NICHT das Verhalten des "
                         "echten Geraets - nur fuer Logikpruefungen")
    ap.add_argument("--no-ident", action="store_true",
                    help="FC17 und FC43 unbeantwortet lassen, wie es hinter "
                         "dem EE11 im Modbus-Modus beobachtet wurde")
    ap.add_argument("--gap-delay", type=float, default=None,
                    help="die Luecke 0x0026-0x0029 nach so vielen Sekunden "
                         "beantworten (echtes Geraet: ~3,6 s) - bildet einen "
                         "EE11 mit langem festem Modbus-Timeout nach. Ohne "
                         "diese Option bleibt die Luecke stumm wie mit 'Auto' "
                         "(nur TCP)")
    ap.add_argument("--exc11", type=float, default=None,
                    help="statt zu schweigen nach so vielen Sekunden mit "
                         "Exception 11 antworten - bildet ein Gateway mit "
                         "fest eingestellter Modbus-Wartezeit nach (nur TCP)")
    ap.add_argument("--busy", type=float, default=0.0,
                    help="nach einer unbeantworteten Anfrage so viele "
                         "Sekunden besetzt bleiben und neue Verbindungen "
                         "abweisen - bildet ein Gateway nach, dessen eigener "
                         "Timeout laenger ist als der des Clients")
    args = ap.parse_args()

    dead = ("exceptions" if args.exceptions
            else "silent" if args.all_silent else "jupiter")
    dev = Device(args.unit, args.corrupt, dead=dead,
                 busy=args.busy)
    dev.no_ident = args.no_ident
    dev.exc11 = args.exc11
    dev.gap_delay = args.gap_delay
    if args.exc11 is not None and not args.busy:
        dev.busy = args.exc11          # besetzt, bis es aufgibt
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(5)
    print("Testgeraet auf 127.0.0.1:%d (%s, tote Adressen: %s)"
          % (args.port, "rtu" if args.rtu else "tcp",
             {"jupiter": "Exception 2, Luecke 0x0026-0x0029 stumm",
              "exceptions": "alle Exception 2",
              "silent": "alle stumm"}[dead]), flush=True)
    handler = serve_rtu if args.rtu else serve_tcp
    while True:
        conn, _ = srv.accept()
        if dev.is_busy():
            conn.close()                      # wie der EE11: abweisen
            continue
        threading.Thread(target=handler, args=(conn, dev),
                         daemon=True).start()


if __name__ == "__main__":
    main()
