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

Die Abhilfe: einen gescheiterten Block Register für Register abfragen. Genau
das tut [`regscan.py`](../tools/regscan.py), und nur deshalb wurde `0x002A`
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

### Ein zweiter Client wird abgewiesen, nicht verschränkt

Gemessen an einem Elfin EE11C, Firmware 1.41.6, `Max Accept` auf 3: während
Home Assistant die Verbindung hielt, wurde ein zweiter Client **abgewiesen**.
Der Konverter nahm die TCP-Verbindung an und schloss sie sofort wieder — 2700
Versuche, 2700 Mal `connection closed by peer`, je 0,465 s, also genau die
Wartezeit des Clients und kein Timeout. Kein einziger verirrter Wert kam durch.

Daraus folgen zwei Dinge.

**Die fehlerhaften Messwerte, die dieses Repository dokumentiert, kamen nicht
von zwei konkurrierenden Programmen.** Sie können es nicht: der Konverter
lässt keine zwei zu. Sie kamen von Bursts *innerhalb* eines Clients — mehrere
Home-Assistant-Sensoren, die im selben Moment feuerten. Genau das haben der
längere Timeout und die Primzahl-Intervalle weiter unten behoben, und deshalb
hat die Behebung gewirkt.

**`Max Accept` zu erhöhen bringt nichts.** Das Feld nimmt 3 an, das Gerät
verhält sich wie 1. Finger weg.

Eine Folge für jeden, der misst: den Scanner nur mit gestopptem zweitem Client
laufen lassen, sonst wird er schlicht ausgesperrt und misst nichts. Ein Wert
bei `closed` in der Aufstellung ist das Symptom.

### Falls doch nötig: die Umsetzung vom Gateway wegnehmen

Als Option lesen, nicht als Empfehlung. An dem hier gemessenen Gerät löst sie
ein Problem, das nicht auftritt — siehe den Ausgangswert am Ende der Seite.
Interessant wird sie, wenn die Zähler für verworfene Werte zu steigen beginnen.

In der Betriebsart **Modbus** macht der Konverter die Umsetzung TCP↔RTU selbst,
und das Einzige zwischen dir und einer veralteten Antwort ist eine 16-Bit-
Transaction-ID, die der Konverter wiederverwendet.

Stell das Protokoll stattdessen auf **None / transparent** und sprich
**Modbus RTU over TCP**: dann laufen die rohen seriellen Telegramme durch, jedes
mit eigener CRC16, und der Client bestimmt die Rahmengrenzen. `regscan.py --rtu`
macht genau das; die native `modbus:`-Integration in Home Assistant mit
`type: rtuovertcp`.

Was das bringt, muss man genau sagen, sonst verspricht man zu viel:

| | |
|---|---|
| **Wird erkannt** | verschmolzene Telegramme (zwei Antworten in einem TCP-Segment), zerrissene oder abgeschnittene Telegramme, alles, was ein übriggebliebenes Byte verschoben hat, eine Antwort von einer anderen Slave-Adresse, eine Byte-Zahl, die nicht zur Anfrage passt |
| **Wird nicht erkannt** | eine veraltete Antwort auf eine *frühere Anfrage von exakt derselben Form*. Ihre Prüfsumme stimmt, denn es ist ein echtes Telegramm — nur die Antwort auf die falsche Frage. Das sieht keine Prüfung auf Protokollebene, in keiner der beiden Betriebsarten |

Gegen den zweiten Fall hilft kein Kniff, nur Disziplin: den Socket vor jeder
Anfrage leerräumen, immer nur einen Client am Bus, mehrfach lesen und die
Mehrheit nehmen, und die Plausibilitätsgrenzen weiter unten anwenden.

Ein zweites, gemessenes Argument für RTU: Ist ein Telegramm kaputt, merkt der
RTU-Client das sofort und wiederholt die Anfrage. Der Modbus-TCP-Client kann das
nicht — er muss bis zum Timeout auf eine passende Transaction-ID warten. Jede
kaputte Antwort kostet dort also einen vollen Timeout, und genau diese Blockade
löst die nächste Kollision aus. Im Test gegen ein simuliertes Gateway, das jede
zweite Antwort verfälschte, brauchte der TCP-Pfad je Störung einen Timeout, der
RTU-Pfad keinen.

Beide Betriebsarten zählen jetzt mit, *warum* Lesungen verworfen wurden, und
geben die Aufstellung am Ende jedes Laufs aus. Damit ist der Vorher/Nachher-
Vergleich einer Gateway-Änderung eine Zahl und kein Eindruck. Der TCP-Wert ist
dabei bauartbedingt eine Untergrenze: der Fehler, den er nicht sehen kann, ist
der, dessentwegen man umstellt.

### Was `--fast` im Scanner absichtlich falsch macht

Die Grenzsuche fragt einen scheiternden Block Register für Register ab. Nur
so findet man ein Register, das isoliert zwischen toten Nachbarn sitzt —
`0x002A` ist genau dieser Fall.

`--fast` überspringt einen toten 8er-Block, nachdem nur dessen beide Enden
geprüft wurden. Bei einem Gerät, das tote Adressen mit Exception 2 ablehnt, ist
das deutlich schneller — und **verliert `0x002A` stillschweigend**; ein Lauf gegen ein simuliertes Gerät hat das bestätigt: 60
Register statt 61. Schlimmer noch, die sechs Adressen dazwischen standen bisher
als `exception 2` im Abzug, obwohl das Gerät dazu nie befragt wurde. Jetzt steht
dort `not probed (--fast)`.

`--fast` taugt für eine grobe Grenzprüfung. Nicht für einen Abzug, den du später
vergleichen willst.

### Was ein vollständiger Scan wirklich kostet, und warum

Zwei Arten von „hier ist nichts", gemessen am 29. September 2026 bei Unit 1 und
Unit 11 gleichermaßen (Einzelheiten in der
[Registerkarte](register-map.de.md#lücke-0x00260x0029)):

- **Fast jede nicht vorhandene Adresse wird mit Exception 2 abgelehnt**, in rund
  0,21 s. Billig, und nie wiederholt — eine Exception ist das Gerät, das spricht.
- **Die vier Adressen `0x0026`–`0x0029` bekommen gar keine Antwort**, und danach
  braucht das Gerät 2–3 s, bis es die nächste Anfrage annimmt.

Die zweite Art betrifft nur vier Adressen, aber sie bestimmt die Untergrenze des
Timeouts. Ein Client-Timeout unter der Besetzt-Zeit verschwendet nicht nur Zeit:
die nächste Anfrage trifft auf ein noch beschäftigtes Gerät, das Gateway mit
nur einem Client weist die neue Verbindung ab, und jede abgewiesene Probe wird
als tote Adresse verbucht. Am Testgerät hat ein Timeout von 0,3 s 7 von 61
Registern verloren, `0x002A` darunter — und war *schneller* fertig als ein
korrekter Lauf. Der Scanner sagt das jetzt in Großbuchstaben, wenn es passiert.

| Einstellungen | Gründlicher Scan, 3 × 1024 Adressen | Alle 65536 Adressen |
|---|---|---|
| Standard: `--timeout 4 --retries 2 --delay 0.45` | ~40 min | ~12 h |
| abgestimmt: `--timeout 4.5 --retries 0 --delay 0.1` | ~20 min | **~6 h** |

Der Einzelregister-Durchlauf über den ganzen Adressraum ist damit eine Nacht,
kein Wochenende. Gelaufen ist er noch nicht.

**Korrektur.** Eine frühere Fassung dieser Seite vom selben Tag behauptete, *jede*
nicht vorhandene Adresse schweige, setzte den gründlichen Scan mit rund 4,4
Stunden an und den vollen Durchlauf mit 84, und schrieb, `--fast` wirke bei
diesem Gerät nicht. Alle drei Aussagen kamen daher, dass die Lücke verallgemeinert
wurde: die toten Adressen, die man sich genauer angesehen hatte — `0x0028` im
alten Abzug, `0x0026` in der Kalibrierung —, liegen beide darin. `--fast` wirkt
hier sehr wohl, überall außer in der Lücke.

**Um eine Gateway-Änderung zu beurteilen, gar nicht erst scannen.** Ein Scan
verbringt seine Zeit im leeren Adressraum, und das sagt über die Verbindung
nichts. `--benchmark` liest stattdessen die bekannten Blöcke immer wieder:
Minuten, und es bildet ab, was ein Client im Alltag tut.

---

## Gemessene Zeiten, 29. September 2026

`regscan.py --calibrate` misst drei Dinge: wie schnell ein echtes Register
antwortet, was eine Anfrage an eine tote Adresse auslöst, und wie bald danach ein
neuer Client wieder eine echte Antwort bekommt. Zweimal gelaufen, einmal mit
`Modbus TimeOut` am EE11 auf *Auto*, einmal mit festen 1000 ms:

| | Auto | fest 1000 ms |
|---|---|---|
| Antwortzeit eines echten Registers, Median | 219 ms | 512 ms |
| Antwortzeit eines echten Registers, maximal | 330 ms | 620 ms |
| Anfrage in die Lücke, `0x0026` | Stille, 10 s | Stille, 10 s — **keine Exception 11** |
| Nächste echte Antwort danach möglich nach | 2–3 s | 1,5–2 s |
| Gewöhnliche nicht vorhandene Adresse, z. B. `0x0030` | Exception 2, 0,21 s | (später gemessen, auf Auto) |

**Die Zeit braucht das Gerät, nicht das Gateway.** Hätte das Gateway gebremst,
hätten feste 1000 ms die Besetzt-Zeit auf etwa eine Sekunde gedrückt. Sie blieb
bei rund zwei. Eine zweite Beobachtung zeigt in dieselbe Richtung: der
Unit-Durchlauf hat 245 nicht vorhandene Unit-IDs mit 2 s Timeout abgefragt, und
keine einzige neue Verbindung wurde abgewiesen. Eine Anfrage an niemanden
beschäftigt den Jupiter nicht, und eine nach einem gewöhnlichen nicht vorhandenen
Register auch nicht — die kommt in einer Fünftelsekunde als Exception 2 zurück.
Eine Anfrage in die Lücke tut es.

**Vermutlich deshalb hat der 5-s-Timeout weiter oben geholfen.** Ein
Client-Timeout von 3 s stand genau auf der Kante einer Besetzt-Zeit von 2–3 s —
manchmal genug, unter Last oft nicht —, sobald ein Client die Lücke berührte, wozu
Karten verleiteten, die den Datenblock bis `0x0027` reichen ließen. 5 s lagen
sicher darüber. Die Abhilfe wurde im September ausprobiert; das hier ist der
wahrscheinlichste Mechanismus dahinter.

**`Modbus TimeOut` auf Auto lassen.** Ein fester Wert brachte keine Exception 11,
keine kürzere Besetzt-Zeit, dafür langsamere und unruhigere Antworten — mit
620 ms bedenklich nah an der 1000-ms-Grenze, jenseits derer der Konverter eine
echte Antwort verwerfen würde. Zehn Messungen beweisen nicht, dass der feste
Wert die Verlangsamung verursacht hat, aber auf der anderen Seite der Waage liegt
nichts.

Was das am Scanner geändert hat: scheitert ein Block, geht er jetzt direkt auf
Einzelregister, statt 8 → 4 → 2 → 1 zu halbieren. Ein toter 8er-Block kostet 9
Proben statt 15, das Ergebnis ist identisch, `0x002A` eingeschlossen. Am
Testgerät: 3454 statt 5700 Anfragen für den gründlichen Scan. Und `--calibrate`
misst jetzt eine gewöhnliche tote Adresse und die Lücke getrennt — die erste
Fassung nahm `0x0026` für beides, und so wurde die Lücke für die Regel gehalten.

---

## Ausgangswert, 29. September 2026

Elfin EE11C, Firmware 1.41.6, Betriebsart Modbus, 115200 8N1 Half Duplex,
`Gap Time` 50 ms, `Modbus TimeOut` auto, Home Assistant für den Lauf gestoppt:

```
regscan.py --benchmark 40      40 Runden über 9 bekannte Blöcke
mode = tcp                     requests = 360
failed_attempts = 0            rejects = {}
seconds = 240,7                jeder Block 40/40
```

Im selben Zeitraum meldete die Home-Assistant-Integration **0 verworfene Werte
bei 11592 Anfragen**.

Hier ist nichts zu reparieren. Der Sinn, die Zahlen aufzuschreiben, ist der
nächste Vergleich: denselben Befehl nach einem Firmware-Update, einer
Kabeländerung oder einem neuen Client im Netz laufen lassen — dann ist der
Unterschied eine Zahl und kein Eindruck.

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
