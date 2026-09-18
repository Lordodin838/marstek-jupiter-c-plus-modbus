# Gateway, Verkabelung und die Eigenheiten, die alles bestimmen

*[English version: gateway.md](gateway.md)*

Das hier ist der Teil, der Leute Tage kostet. Die Registerkarte ist die leichte
Hälfte; den Bus dazu zu bringen, *richtige* Werte zu liefern, ist die schwere —
und falsche Werte melden sich nicht von selbst.

---

## Verkabelung

RS485 am Jupiter C Plus. Laut
[danielrahns Notizen](https://github.com/danielrahn/marstek-jupiter-c-plus)
ist **Pin 3 (+5 V) offenbar nicht belegt** — den Wandler separat versorgen.

Gebraucht werden nur A/B/GND.

## Wandler: Elfin EE11 (RS485 → Ethernet)

Funktionierende Einstellungen:

| Einstellung | Wert |
|---|---|
| Baudrate | 115200 |
| Protokoll | Modbus TCP |
| Port | 502 |
| Route | UART |
| Modbus Unit / Slave-ID | 1 |

Jeder RS485-TCP-Wandler tut es. Der EE11 ist der, an dem hier gemessen wurde, und
sein Fehlermodus — unten beschrieben — ist der ganzen Klasse billiger Wandler
gemeinsam.

---

## Die drei Geräteeigenschaften

Alles andere in diesem Repository folgt daraus. Wer einen eigenen Client schreibt,
sollte sie vorher kennen.

### 1. Höchstens 8 Register pro Anfrage

Ab 9 antwortet das Gerät mit **Exception 3 (illegal data value)**. Kein
Teilergebnis — eine Verweigerung.

### 2. Eine Anfrage, die über einen gültigen Bereich hinausreicht, scheitert *komplett*

Das ist die, die weh tut. 8 Register ab `0x0020` decken `0x0020`–`0x0027` ab. Da
`0x0026` und `0x0027` nicht existieren, **scheitert die ganze Anfrage** — inklusive
der sechs völlig gültigen Register am Anfang.

Praktische Folge für jeden, der einen Scanner schreibt: **ein starres 8er-Raster
verliert an jeder Bereichsgrenze stillschweigend Register.** Der erste Scanlauf an
diesem Gerät hat `0x0001`–`0x0007` komplett übersehen, weil der Rasterblock bei
`0x0000` begann und `0x0000` nicht existiert. Genauso fielen `0x1008`–`0x100A`
heraus, weil dieser Block bis `0x100F` gereicht hätte.

Die Abhilfe: fehlgeschlagene Blöcke bis auf Einzelregister hinunter halbieren.
Genau das tut [`regscan.py`](../tools/regscan.py), und nur deshalb wurde `0x002A`
überhaupt gefunden.

### 3. FC4 wird nicht unterstützt

Input Register (Funktionscode 4) antworten mit **Exception 1 (illegal function)**.
Alles ist Holding Register, gelesen mit FC3.

---

## Der Fehlermodus, der falsche Daten erzeugt

**Der Wandler reicht fremde Antworten durch, wenn die Transaction-ID passt.**

Der Elfin bearbeitet immer nur eine Anfrage. Unter Last — mehrere Clients, oder
einer, der schnell pollt — trifft die Antwort auf Anfrage N ein, nachdem der
Client sie schon aufgegeben und auf N+1 weitergeschaltet hat. Passen die
Transaction-IDs zusammen, nimmt der Client sie an. **Der Wert landet im falschen
Sensor.**

Wie das in der Praxis aussieht:

- Ein SoC-Sensor, der `3255` % anzeigt.
- Register, die „Werte haben", obwohl sie tot sein müssten — daher stammen die
  Phantomwerte auf `0x0026`/`0x0027` in älteren Karten.
- Werte, die plausibel aussehen, aber zum Nachbarregister gehören. Das ist der
  weitaus schlimmere Fall, weil nichts daran auffällt.

Es wirft keinen Fehler. Es loggt nichts. Man findet es, indem einem auffällt, dass
ein Tagesminimum oder -maximum unmöglich ist.

### Was es tatsächlich behoben hat

Drei Änderungen, nach Wirkung sortiert:

**1. Timeout hochsetzen.** 3 s war zu knapp: der Client gab auf, während eine
Antwort noch unterwegs war, und diese Antwort kollidierte dann mit der nächsten
Anfrage. **5 s hat es behoben.** Das war die mit Abstand größte Verbesserung.

**2. Abfragen entzerren — Primzahl-Intervalle.** Siehe unten.

**3. Nicht 1-Register- und 2-Register-Lesungen (uint32) im selben Takt mischen.**
Schon das Versetzen hilft für sich genommen.

Gemessenes Ergebnis: die Anfragerate fiel von ~76/min auf ~48/min, die Bursts von
22 gleichzeitigen Anfragen verschwanden, und die Zahl unplausibler Werte über die
folgenden vier Tage lag bei **null** — gegenüber Tagesbereichen des SoC von 0–3255
und 0–3308 an den zwei Tagen davor.

### Warum Primzahl-Intervalle

Wenn mehrere Sensoren im Takt 10 s, 20 s, 30 s und 60 s abfragen, feuern alle
zusammen **alle 60 s gleichzeitig**. Genau dieser Burst ist die Bedingung, die der
Wandler nicht verkraftet, und er wiederholt sich nach Fahrplan — die Korruption
ist also periodisch und sieht aus wie ein Gerätefehler.

Gibt man jedem Sensor ein eigenes Primzahl-Intervall — 31, 61, 67, 127, 197, 199,
211, 223, 227, 293, 307, 311, 313 s — setzen sich die Bursts nicht wieder zusammen.
Zwei Intervalle ohne gemeinsamen Teiler treffen sich nur alle *n×m* Sekunden statt
alle *max(n,m)*.

Schnell sein muss nur die Handvoll Register, gegen die tatsächlich geregelt wird.
Im Beispielpaket laufen sechs Register auf 10 s (vier PV-Leistungen,
Netzleistung, SoC); alles andere auf einer Primzahl zwischen 31 s und etwa einer
Stunde.

### Gegenmaßnahme im Scanner

[`regscan.py`](../tools/regscan.py) baut seine Modbus-Rahmen von Hand statt
pymodbus zu verwenden, und zwar genau deshalb, um **Transaction-ID, Protokoll-ID,
Unit und Länge selbst zu prüfen** und fremde Antworten zu verwerfen — bis zu vier
je Lesung — statt ihnen zu vertrauen.

Zusätzlich liest er alles dreimal und übernimmt einen Wert nur, wenn er mindestens
zweimal aufgetaucht ist. Alles andere steht im Bericht als `NEIN` statt still
verwendet zu werden. Diese doppelte Absicherung ist der Grund, warum sich die
Abzüge in [`dumps/`](../dumps) sinnvoll gegeneinander halten lassen.

---

## Plausibilitätsprüfungen, die sich zu automatisieren lohnen

Nach jeder Änderung — Firmware, Verkabelung, Abfragetakt — diese prüfen. Sie
zeigen eine verschobene Registerkarte sofort:

| Prüfung | Erwartung |
|---|---|
| SoC (`0x0010`) | 0–100, nie darüber |
| Batteriespannung (`0x000F`) | um 52 V bei einem 16S-LFP-Pack |
| Zellspannungen (`0x0020`/`0x0021`) | um 3,3 V, max ≥ min |
| `0x0020` × 16 | ≈ `0x000F` |
| Gerätetyp (`0x0025`) | `0`, konstant. **Ändert sich das, hat sich die Karte verschoben** |
| PV-Statusflags (`0x1004`–`0x1007`) | nachts alle 0 |

Tagesminima und -maxima sind der billigste Detektor: ein unmögliches Maximum an
irgendeinem Sensor heißt, dass der Bus aus dem Tritt gerät — auch wenn der aktuelle
Wert gerade gut aussieht.
