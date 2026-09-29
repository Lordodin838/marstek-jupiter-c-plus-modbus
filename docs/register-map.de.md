# Registerkarte — Marstek Jupiter C Plus

*[English version: register-map.md](register-map.md)*

Firmware `142.37.213.110`, identisch auf `138.37.213.110`.
Alle Register sind **Holding Register, gelesen mit FC3**. FC4 (Input Register)
wird nicht unterstützt — das Gerät antwortet mit Exception 1.

Die Wortreihenfolge bei 32-Bit-Werten ist **Big Endian, High-Word zuerst**. Weder
Byte- noch Word-Swap nötig.

## Sicherheitsangaben

| Stufe | Bedeutung |
|---|---|
| **bestätigt** | Gegen eine unabhängige Quelle geprüft (Cloud-/MQTT-Pfad, zweites Messgerät oder eine rechnerische Identität), oder selbsterklärend richtig |
| **plausibel** | Verhält sich über Tage hinweg stimmig zur angegebenen Bedeutung, aber nichts Unabhängiges bestätigt es |
| **unbekannt** | Antwortet, Wert festgehalten, Bedeutung ungeklärt |

---

## Datenblock `0x0001`–`0x0025`

| Adr | Bedeutung | Typ | Faktor | Einheit | Sicherheit |
|---|---|---|---|---|---|
| `0x0001` | PV1 Spannung | u16 | 0,1 | V | bestätigt |
| `0x0002` | PV1 Strom | u16 | 0,1 | A | bestätigt |
| `0x0003` | PV1 Leistung | u16 | 1 | W | bestätigt |
| `0x0004` | PV2 Spannung | u16 | 0,1 | V | bestätigt |
| `0x0005` | PV2 Strom | u16 | 0,1 | A | bestätigt |
| `0x0006` | PV2 Leistung | u16 | 1 | W | bestätigt |
| `0x0007` | PV3 Spannung | u16 | 0,1 | V | bestätigt |
| `0x0008` | PV3 Strom | u16 | 0,1 | A | bestätigt |
| `0x0009` | PV3 Leistung | u16 | 1 | W | bestätigt |
| `0x000A` | PV4 Spannung | u16 | 0,1 | V | bestätigt |
| `0x000B` | PV4 Strom | u16 | 0,1 | A | bestätigt |
| `0x000C` | PV4 Leistung | u16 | 1 | W | bestätigt |
| `0x000D` | Netzleistung | **i16** | 1 | W | bestätigt |
| `0x000E` | Temperatur (intern/Umgebung) | u16 | 0,1 | °C | plausibel |
| `0x000F` | Batteriespannung | u16 | 0,1 | V | bestätigt |
| `0x0010` | Batterie SoC | u16 | 1 | % | bestätigt |
| `0x0011` | **Fehlercode** | u16 | 1 | — | bestätigt — [siehe unten](#0x0011--fehlercode) |
| `0x0012` | vermuteter zweiter Alarm-/Warncode | i16 | 1 | — | unbekannt |
| `0x0013`+`0x0014` | Tagesertrag | u32 | 0,01 | kWh | bestätigt |
| `0x0015`+`0x0016` | Monatsertrag | u32 | 0,01 | kWh | bestätigt |
| `0x0017`+`0x0018` | Tageseinspeisung | u32 | 0,01 | kWh | bestätigt |
| `0x0019`+`0x001A` | Monatseinspeisung | u32 | 0,01 | kWh | bestätigt |
| `0x001B` | Geräte-ID | u16 | 1 | — | bestätigt |
| `0x001C` | EMS-Firmwarestand | u16 | 1 | — | bestätigt |
| `0x001D` | INV-Firmwarestand | u16 | 1 | — | bestätigt |
| `0x001E` | MPPT-Firmwarestand | u16 | 1 | — | bestätigt |
| `0x001F` | BMS-Firmwarestand | u16 | 1 | — | bestätigt |
| `0x0020` | Zellspannung, Maximum | u16 | 0,001 | V | bestätigt |
| `0x0021` | Zellspannung, Minimum | u16 | 0,001 | V | bestätigt |
| `0x0022` | Display-Firmwarestand | u16 | 1 | — | bestätigt |
| `0x0023` | — | u16 | ? | ? | **unbekannt** — [siehe unten](#0x0023--ungeklärt) |
| `0x0024` | — | u16 | ? | ? | unbekannt, konstant 0 |
| `0x0025` | Gerätetyp (`0` hier; vollständige Liste unten) | u16 | 1 | — | bestätigt |

**`0x000D` ist vorzeichenbehaftet.** Als u16 gelesen liefert es Unsinn, sobald das
Gerät bezieht statt einzuspeisen. Das Vorzeichen folgt dem CT: positiv heißt
Leistung in der Richtung, die die Klemme als Bezug zählt. Auch Marsteks eigene
Tabelle führt es als u16 — das stimmt nicht.

**`0x0020` × 16 ≈ `0x000F`.** Diese Identität bestätigt beide: 16 Zellen in Reihe,
maximale Zellspannung mal 16 folgt der Packspannung über den ganzen Bereich.

**Es gibt kein Register für die DC-Batterieleistung.** Die muss gerechnet werden
(PV-Leistung minus Netzleistung, oder Packspannung mal einem Strom, den es hier
nicht gibt).

**Entladetiefe / SoC-Untergrenze steht nicht auf Modbus.** Firmware 140+ hat eine
einstellbare Entladegrenze gebracht, erreichbar ist sie aber nur über App und
Cloud-/MQTT-Pfad. Über das Update 138 → 142 kam in der Registerkarte nichts dazu —
belegt durch einen byteweisen Vergleich zweier vollständiger Registerabzüge vor
und nach dem Update.

### Gerätetyp `0x0025`

Marsteks offizielle Tabelle führt sechs Werte auf, für Jupiter C und Jupiter E
gemeinsam. Das Gerät, an dem hier gemessen wurde, liefert `0`.

| Wert | Gerät |
|---|---|
| 0 | Jupiter C 800 W |
| 1 | Jupiter C 1000 W |
| 2 | Jupiter C 600 W |
| 3 | Jupiter E 800 W |
| 4 | Jupiter E 1000 W |
| 5 | Jupiter E 600 W |

Dass dieselbe Tabelle beide Modelle abdeckt, ist der Grund, diese Registerkarte
auch beim Jupiter E zu **erwarten** — erwarten, nicht wissen: gemessen wurde
hier nichts an einem E. Messwerte von einem E gerne als Issue.

---

## Lücke `0x0026`–`0x0029`

Antworten nicht. Jede Anfrage, die sie überlappt, scheitert vollständig — auch
eine, die im gültigen Block beginnt, siehe
[gateway.de.md](gateway.de.md#die-drei-geräteeigenschaften).

Frühere Karten führten Werte auf `0x0026`/`0x0027`. Das waren verirrte Antworten
eines aus dem Tritt geratenen TCP-Gateways, keine Gerätedaten.

## Isoliertes Register `0x002A`

| Adr | Bedeutung | Typ | Gesehener Wert | Sicherheit |
|---|---|---|---|---|
| `0x002A` | — | u16 | `1` | unbekannt |

Isoliert: `0x0029` davor und `0x002B` danach sind beide tot. In keiner
veröffentlichten Registerkarte enthalten. Über Tage konstant `1`.

Methodisch bemerkenswert: eine blockweise Suche **kann** dieses Register nicht
finden, weil jede 8er-Stichprobe, die es überlappt, auch tote Adressen überlappt
und deshalb komplett scheitert. Es taucht nur auf, wenn der Scanner fehlgeschlagene
Blöcke bis auf Einzelregister hinunter halbiert.

`0x002B`–`0x00FF`: keine Antwort.

---

## Statusblock `0x1000`–`0x100A`

| Adr | Bedeutung | Gesehener Wert | Sicherheit |
|---|---|---|---|
| `0x1000` | Statusflag | 1 | unbekannt |
| `0x1001` | Statusflag | 1 und **2** | unbekannt |
| `0x1002` | Statusflag | 1 | unbekannt |
| `0x1003` | Statusflag | 1 | unbekannt |
| `0x1004` | PV1 Arbeitsstatus | 0/1 | bestätigt |
| `0x1005` | PV2 Arbeitsstatus | 0/1 | bestätigt |
| `0x1006` | PV3 Arbeitsstatus | 0/1 | bestätigt |
| `0x1007` | PV4 Arbeitsstatus | 0/1 | bestätigt |
| `0x1008` | Wechselrichter Arbeitsstatus | 0/1 | bestätigt |
| `0x1009` | **Lade-/Entlade-Freigabe** | 0 und 1 | plausibel — [siehe unten](#0x1009-und-0x100a--die-offizielle-tabelle) |
| `0x100A` | **Zulässige Entladeleistung** | 0 | plausibel — [siehe unten](#0x1009-und-0x100a--die-offizielle-tabelle) |

`0x1001` wurde schon auf **2** gesehen, nicht nur auf 0 oder 1. Was dieser Block
auch immer ist, er ist nicht rein boolesch — also nicht blind auf Binärsensoren
abbilden, wie es bei `0x1004`–`0x1008` geht. `0x1001` ändert sich über die Zeit, trägt also eine
Information; welche, hat noch niemand herausgefunden. Für `0x1009` und
`0x100A` gibt es in Marsteks eigener Tabelle eine Bedeutung — siehe unten.

Die PV-Statusflags folgen exakt dem Tageslicht (nachts alle vier auf 0), das ist
die Bestätigung.

`0x100B`–`0x10FF`: keine Antwort.

---

### `0x1009` und `0x100A` — die offizielle Tabelle

Marsteks eigene Registertabelle — die dem Jupiter E beiliegt und in
[stevedee78/Marstek-Jupiter-E-Modbus-ESPhome](https://github.com/stevedee78/Marstek-Jupiter-E-Modbus-ESPhome)
abfotografiert ist — gibt beiden eine Bedeutung:

| Adr | Offizieller Name | Angaben aus der Tabelle |
|---|---|---|
| `0x1009` | charge and discharge marker | MSB: 1 = Laden erlaubt, 0 = kein Laden. LSB: 1 = Entladen erlaubt, 0 = kein Entladen. Beispielwert `0x11` = „chargeable and dischargeable" |
| `0x100A` | allowable discharge power | in W, „maximum current permissible battery discharge" |

Zwei Vorbehalte, bevor das jemand auf Entitäten abbildet:

**Die Bitlage ist nicht belegt.** Die Tabelle spricht von MSB und LSB, ihr
eigener Beispielwert `0x11` passt aber zu *Nibbles* (`0x10` Laden, `0x01`
Entladen), nicht zu Bytes (`0x0100` / `0x0001`). Beide Lesarten vertragen sich
mit dem hier Gemessenen (0 und 1): ein voller Akku, der entladen, aber nicht
laden darf, liest sich so wie so als 1. Bis jemand einen vollständigen
Ladezyklus mitschreibt, also defensiv dekodieren.

**Dass `0x100A` auf 0 steht, ist ein Messwert, kein totes Register.** Es stand
über den ganzen Beobachtungszeitraum auf 0. Bei Entladesperre ist das
plausibel; ein Wert ungleich 0 wurde hier nie gesehen, die Skalierung ist also
ungeprüft.

---

## MAC-Adresse `0x1100`–`0x1105`

Beispielgerät, Adresse `A4:C1:38:9F:2B:7E`:

| Adr | Inhalt | ASCII |
|---|---|---|
| `0x1100` | `0x6134` | `a4` |
| `0x1101` | `0x6331` | `c1` |
| `0x1102` | `0x3338` | `38` |
| `0x1103` | `0x3966` | `9f` |
| `0x1104` | `0x3262` | `2b` |
| `0x1105` | `0x3765` | `7e` |

Zwölf ASCII-Zeichen, Hex in Kleinbuchstaben, innerhalb jedes Registers High-Byte
zuerst. Zusammengesetzt: `a4c1389f2b7e` → `A4:C1:38:9F:2B:7E`.

Gegengeprüft gegen die Bluetooth-Adresse, die das Gerät aussendet — diese
Gegenprobe macht das Register sicher statt bloß plausibel.

`0x1106`–`0x11FF`: keine Antwort.

---

## Firmware des Kommunikationsmoduls `0x1200`–`0x1205`

Gleiche Codierung: zwölf ASCII-Ziffern, die einen Build-Stempel ergeben.

```
202512040647   ->   04.12.2025, Build 0647
```

**Getrennt** von `0x001B`–`0x001F` geführt und von einem EMS-Update unberührt. Die
einzige Möglichkeit, einen neuen Stand des Kommunikationsmoduls zu bemerken.

`0x1206`–`0x13FF`: keine Antwort.

---

## Schreibregister `0x4000`+

`0x4000`–`0x43FF` liefern auf FC3 keine Daten. **Nicht durch Schreiben
untersucht** — alles hier ist bewusst nur lesend, und an einem netzgekoppelten
Wechselrichter undokumentierte Schreibregister auszuprobieren ist ein guter Weg,
um herauszufinden, was ein undokumentiertes Schreiben anrichtet.

Marsteks eigene Tabelle dokumentiert fünf davon, für Funktionscode `0x06`:

| Adr | Offizieller Name | Werte laut Tabelle |
|---|---|---|
| `0x4000` | surplus power feed-in | 0 = aus, 1 = ein |
| `0x4001` | reset the jupiter | 1 = Reset |
| `0x4002` | select EMS mode | 0 = internes EMS, 1 = externes EMS (RS485) |
| `0x4003` | set the grid-connected power | Bereich 30–800 W |
| `0x4004` | set the Device ID | Bereich `0x08`–`0xF7` |

Dokumentiert heißt nicht funktionierend: Im Home-Assistant-Forum berichtet ein
Besitzer eines Jupiter C Plus, dass Schreibzugriffe auf `0x4003`
[ignoriert werden](https://community.home-assistant.io/t/marstek-jupiter-c-plus-modbus-tcp-rs485-write-commands-ignored-register-16387/1021269),
per FC06 wie per FC16 und auch nach Umschalten von `0x4002`. Die Tabelle markiert
außerdem einen Teil ihres Inhalts als „derzeit nicht unterstützt"; welchen, ist
auf dem verfügbaren Foto nicht zu erkennen.

---

## Alles Übrige

Grobsuche über den vollen 16-Bit-Adressraum (`0x0000`–`0xFFFF`), zwei
8er-Stichproben je 256er-Seite, 244 Seiten außerhalb der obigen Bereiche:
**keine weitere Seite antwortet.**

Einschränkung, klar gesagt: das findet Blöcke, keine Einzelgänger. `0x002A`
beweist, dass es solche Register gibt. In den Lücken können weitere stecken.

### Andere Funktionscodes

`regscan.py --probe`, 29. September 2026, über den EE11 in der Betriebsart Modbus:

| Code | Ergebnis |
|---|---|
| FC1 Coils lesen | Exception 1 — **keine Coils** |
| FC2 Discrete Inputs lesen | Exception 1 — **keine Discrete Inputs** |
| FC4 Input Register lesen | Exception 1 |
| FC17 Report Server ID | keine Antwort |
| FC43/14 Gerätekennung | keine Antwort |

Coils und Discrete Inputs sind eigene Adressräume; dass Holding Register belegt
sind, sagt über sie nichts. Jetzt ist es gemessen: das Gerät bietet Holding
Register an und sonst nichts.

FC17 und FC43 verschluckt höchstwahrscheinlich der Konverter, nicht das Gerät:
dasselbe Gerät beantwortet FC1, FC2 und FC4 ordentlich mit „nicht unterstützt“,
und ein Modbus-Stack, der das tut, beantwortet FC17 und FC43 üblicherweise
genauso. Klären ließe es sich nur mit dem EE11 im transparenten Modus. Nicht
gemacht.

### Unit-IDs

Das Gerät antwortet auf Unit **1 und 11**, ein Gerät unter zwei Adressen. Siehe
[das README](../README.de.md#unit-id-11--dasselbe-gerät-unter-einer-zweiten-adresse).

---

## `0x0011` — Fehlercode

Das Register enthält den **Dezimalwert des hexadezimalen Fehlercodes** aus dem
Handbuch:

| Register zeigt | Hex | Handbuch nennt es |
|---|---|---|
| `0` | — | kein Fehler |
| `1028` | `0x404` | netzseitiger Überhitzungsschutz |
| `1062` | `0x426` | *undokumentiert — Lücke zwischen 422 und 440 im Handbuch* |
| `1483` | `0x5CB` | Netzwerkanomalie |

Vollständige Tabelle: [fault-codes.md](fault-codes.md).

Beleg: während eines Störfalls wurden Modbus-Register und Cloud-Fehlercode
nebeneinander mitgeschrieben. In 4 von 4 Abfragen innerhalb des Störfensters stand
das Register auf 1062, während die Cloud 426 meldete. Ein dreiminütiges
Störfenster lieferte keine passende Modbus-Probe — es fiel zwischen zwei
10-Minuten-Abfragen. Eine Lücke in der Abtastung, kein Widerspruch. Wer kurze
Störungen erwischen will, fragt schneller als alle 60 s ab.

## `0x0023` — ungeklärt

Beobachtetes Band 36–39, schmal und träge. Durch direkten Vergleich ausgeschlossen:

- **nicht Batteriestrom** — folgt Lade-/Entladewechseln nicht
- **nicht Batteriespannung** — folgt `0x000F` nicht
- **nicht SoC** — folgt `0x0010` nicht

Über das Update 138 → 142 sprang der Wert von ~51 auf ~37 und blieb im neuen Band.
Eine physikalische Messgröße sollte das nicht tun. Denkbar: ein Zähler, ein
interner Zustandscode oder ein Kalibrierwert.

Wer dieses Gerät hat: den Wert zusammen mit Packtemperatur und Zellzahl zu melden
würde weiterhelfen.
