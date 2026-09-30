#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Die grosse Suche: jede der 65536 Adressen einzeln, FC3, eine Unit.

Warum einzeln: ein 8er-Block, der auch nur eine tote Adresse beruehrt,
scheitert komplett - ein Register zwischen toten Nachbarn findet nur die
Einzelabfrage. Einzeln kostet eine tote Adresse auf dem Jupiter ~0,2 s
(Exception 2), das ganze Feld also gut 6 h.

Gelaufen am 30.09.2026 an einem Jupiter C Plus, Firmware 142: 65 Register,
65465 x Exception 2, 6 Aussetzer, die beim Nachfragen Exception 2 lieferten.
Siehe docs/register-map.md, "Everything else".

Alles, was NICHT Exception 2 ist, ist ein Treffer oder eine Auffaelligkeit:
  - Wert                     -> Register gefunden
  - andere Exception (1,3,4) -> Geraet sagt etwas anderes als "gibt es nicht"
  - Stille / Fehler          -> nach einer Pause einmal nachgefragt. Kommt
                                dann Exception 2, war es ein Aussetzer; bleibt
                                es still, ist die Adresse "stumm"

Fortsetzbar: jede Adresse landet sofort als Zeile in der .jsonl-Datei.
Abbruch mit Ctrl-C, gleicher Aufruf mit --weiter <datei> setzt dort fort.

Alle 1024 Adressen wird 0x0010 (SoC) als Kontrolle gelesen. Antwortet die
nicht mehr, pausiert das Skript und wartet, statt Tausende Adressen
falsch als "stumm" zu notieren.

Voraussetzungen:
  - kein anderer Client am Gateway, z. B. die Home-Assistant-Integration
    deaktivieren (der EE11 nimmt nur einen Client)
  - EE11: Modbus TimeOut FEST auf 5000 ms (nicht Auto), sonst sehen
    langsame Register wie 0x0026-0x0029 aus wie tote

Nur lesend.

    python3 vollsuche.py --host 192.168.1.50
    python3 vollsuche.py --host 192.168.1.50 --weiter vollsuche_XXXX.jsonl
    python3 vollsuche.py --auswerten vollsuche_XXXX.jsonl

Ausgabe auf Deutsch, wie der Referenzabzug in dumps/.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

HIER = os.path.dirname(os.path.abspath(__file__))
# im Repository liegt regscan.py daneben; in einer Arbeitskopie ausserhalb
# (test/ neben repo/) unter ../repo/tools
sys.path.insert(0, os.path.join(HIER, "..", "repo", "tools"))
sys.path.insert(0, HIER)
import regscan  # noqa: E402

KONTROLLE = 0x0010
KONTROLL_ABSTAND = 1024


class Leitung:
    """Bus mit Wiederverbinden, das eine lange Nacht uebersteht."""

    def __init__(self, host, port, unit, timeout):
        self.args = (host, port, unit, timeout)
        self.bus = None
        self.verbinde()

    def verbinde(self):
        wartezeit = 5
        seit = time.time()
        while True:
            try:
                alt = self.bus
                if alt:
                    alt.close()
                self.bus = regscan.Bus(*self.args)
                if alt:
                    # Zaehler ueber das Wiederverbinden retten, sonst
                    # zeigt der Qualitaetsbericht nur das letzte Stueck
                    self.bus.reads = alt.reads
                    self.bus.failed = alt.failed
                    self.bus.rejects = alt.rejects
                return
            except OSError as exc:
                if time.time() - seit > 600:
                    raise SystemExit("Seit 10 Minuten keine Verbindung (%s). "
                                     "Abbruch - mit --weiter fortsetzen."
                                     % exc)
                print("    keine Verbindung (%s), neuer Versuch in %d s"
                      % (exc, wartezeit), flush=True)
                time.sleep(wartezeit)
                wartezeit = min(60, wartezeit * 2)

    def lies(self, adr):
        t0 = time.time()
        try:
            werte, notiz = self.bus.read(adr, 1)
        except OSError as exc:
            werte, notiz = None, "verbindung: %s" % exc
            self.verbinde()
        return werte, notiz, time.time() - t0


def kontrolle(leitung):
    """True, wenn 0x0010 antwortet. Sonst warten, bis es wieder geht."""
    for versuch in range(30):
        werte, notiz, _ = leitung.lies(KONTROLLE)
        if werte is not None:
            if versuch:
                print("    Kontrolle wieder ok nach %d Versuchen" % versuch,
                      flush=True)
            return
        print("    Kontrolle 0x0010 ohne Antwort (%s) - warte 20 s"
              % notiz, flush=True)
        time.sleep(20)
        leitung.verbinde()
    raise SystemExit("Kontrollregister antwortet seit 10 Minuten nicht. "
                     "Abbruch - mit --weiter fortsetzen.")


def lade(pfad):
    erledigt = {}
    with open(pfad) as fh:
        for zeile in fh:
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                e = json.loads(zeile)
            except ValueError:
                continue          # halbe letzte Zeile nach Abbruch
            if "a" in e:
                erledigt[int(e["a"], 16)] = e
    return erledigt


def endgueltig(e):
    """Was die Adresse am Ende gesagt hat - die Nachfrage zaehlt, wenn es
    eine gab. Eine Stille, auf die beim zweiten Versuch Exception 2 folgt,
    war ein Aussetzer von Netz oder Gateway, keine Eigenschaft der Adresse."""
    if "w" in e:
        return "wert"
    zweit = e.get("zweit") or {}
    if e.get("n") == "exception 2" or zweit.get("n") == "exception 2":
        return "exception 2"
    return "anders"


def zusammenfassung(erledigt, pfad):
    werte = sorted(a for a, e in erledigt.items() if endgueltig(e) == "wert")
    anders = sorted(a for a, e in erledigt.items()
                    if endgueltig(e) == "anders")
    aussetzer = sorted(a for a, e in erledigt.items()
                       if "zweit" in e and endgueltig(e) != "anders")

    def bereiche(adressen):
        out, start, vor = [], None, None
        for a in adressen:
            if start is None:
                start = vor = a
            elif a == vor + 1:
                vor = a
            else:
                out.append((start, vor))
                start = vor = a
        if start is not None:
            out.append((start, vor))
        return out

    zeilen = ["Vollsuche %s" % os.path.basename(pfad),
              "%d Adressen geprueft, %d mit Wert, %d weder Wert noch "
              "Exception 2, %d Aussetzer" % (len(erledigt), len(werte),
                                             len(anders), len(aussetzer)),
              "", "Bereiche mit Wert:"]
    for s, e in bereiche(werte):
        zeilen.append("  0x%04X-0x%04X  (%d)" % (s, e, e - s + 1))
    zeilen += ["", "Auffaellig (auch beim Nachfragen weder Wert noch "
                   "Exception 2):"]
    if not anders:
        zeilen.append("  keine")
    for a in anders:
        e = erledigt[a]
        zweit = e.get("zweit") or {}
        zeilen.append("  0x%04X  %s  (%.1f s) / nachgefragt: %s"
                      % (a, e.get("n"), e.get("s", 0), zweit.get("n", "-")))
    zeilen += ["", "Aussetzer (erst ohne Antwort, beim Nachfragen "
                   "eindeutig):"]
    if not aussetzer:
        zeilen.append("  keine")
    for a in aussetzer:
        e = erledigt[a]
        zeilen.append("  0x%04X  %s  (%.1f s) -> %s"
                      % (a, e.get("n"), e.get("s", 0),
                         "Wert %d" % e["w"] if "w" in e
                         else e["zweit"].get("n")))
    zeilen += ["", "Langsame Antworten (> 1 s) mit Wert:"]
    langsam = [a for a in werte if erledigt[a].get("s", 0) > 1.0]
    zeilen += ["  0x%04X  %.1f s" % (a, erledigt[a]["s"]) for a in langsam] \
        or ["  keine"]
    text = "\n".join(zeilen)
    print("\n" + text)
    with open(pfad.replace(".jsonl", ".txt"), "w") as fh:
        fh.write(text + "\n")
    print("\ngeschrieben:\n  %s\n  %s" % (pfad, pfad.replace(".jsonl",
                                                              ".txt")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--unit", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=6.0,
                    help="Sekunden (Standard 6.0 - laenger als die 5000 ms "
                         "am EE11, damit dessen Antwort noch ankommt)")
    ap.add_argument("--delay", type=float, default=0.1)
    ap.add_argument("--von", type=lambda s: int(s, 0), default=0x0000)
    ap.add_argument("--bis", type=lambda s: int(s, 0), default=0xFFFF)
    ap.add_argument("--weiter", metavar="DATEI.jsonl",
                    help="abgebrochenen Lauf fortsetzen")
    ap.add_argument("--auswerten", metavar="DATEI.jsonl",
                    help="nichts abfragen, nur die Zusammenfassung einer "
                         "vorhandenen Datei (neu) schreiben")
    args = ap.parse_args()

    if args.auswerten:
        pfad = os.path.abspath(args.auswerten)
        zusammenfassung(lade(pfad), pfad)
        return 0
    if not args.host:
        ap.error("--host fehlt")

    regscan.DELAY = args.delay
    regscan.RETRY = 0

    if args.weiter:
        pfad = os.path.abspath(args.weiter)
        erledigt = lade(pfad)
        print("Fortsetzung: %d Adressen schon erledigt" % len(erledigt))
    else:
        pfad = os.path.join(HIER, "vollsuche_u%d_%s.jsonl" % (
            args.unit, datetime.now().strftime("%Y%m%d-%H%M")))
        erledigt = {}

    offen = [a for a in range(args.von, args.bis + 1) if a not in erledigt]
    print("Unit %d, 0x%04X-0x%04X, %d Adressen offen, Datei:\n  %s"
          % (args.unit, args.von, args.bis, len(offen), pfad), flush=True)

    leitung = Leitung(args.host, args.port, args.unit, args.timeout)
    kontrolle(leitung)

    t_start = time.time()
    letzte_meldung = t_start
    treffer = sum(1 for e in erledigt.values() if "w" in e)
    auffaellig = 0
    try:
        with open(pfad, "a") as fh:
            for i, adr in enumerate(offen, 1):
                if adr % KONTROLL_ABSTAND == 0:
                    kontrolle(leitung)

                werte, notiz, dauer = leitung.lies(adr)
                zweit = None
                if werte is None and not (notiz or "").startswith(
                        "exception"):
                    # Stille: Gateway erholen lassen, dann einmal nachfragen
                    time.sleep(3.0)
                    leitung.verbinde()
                    werte, notiz2, dauer2 = leitung.lies(adr)
                    zweit = {"n": notiz2, "s": round(dauer2, 2)}
                    if werte is not None:
                        dauer = dauer2
                    else:
                        time.sleep(3.0)
                        leitung.verbinde()

                e = {"a": "0x%04X" % adr, "s": round(dauer, 2)}
                if werte is not None:
                    e["w"] = werte[0]
                    treffer += 1
                    if adr > 0x002A or dauer > 1.0:
                        print("  0x%04X = %d  (%.2f s)" % (adr, werte[0],
                                                           dauer), flush=True)
                else:
                    e["n"] = notiz
                    if notiz != "exception 2":
                        auffaellig += 1
                        print("  0x%04X  %s" % (adr, notiz), flush=True)
                if zweit:
                    e["zweit"] = zweit
                fh.write(json.dumps(e) + "\n")
                fh.flush()

                if time.time() - letzte_meldung >= 60:
                    rate = i / (time.time() - t_start)
                    rest = (len(offen) - i) / rate if rate else 0
                    print("    ... 0x%04X, %d/%d, %.1f Adr/s, %d mit Wert, "
                          "%d auffaellig, fertig ca. %s"
                          % (adr, i, len(offen), rate, treffer, auffaellig,
                             (datetime.now() + timedelta(seconds=rest))
                             .strftime("%H:%M")), flush=True)
                    letzte_meldung = time.time()
    except KeyboardInterrupt:
        print("\nAbgebrochen. Fortsetzen mit:\n  python3 vollsuche.py "
              "--host %s --weiter %s" % (args.host, pfad))
        return 1
    finally:
        leitung.bus.close()
        print("\n" + regscan.quality_report(leitung.bus)
              if hasattr(regscan, "quality_report") else "")

    zusammenfassung(lade(pfad), pfad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
