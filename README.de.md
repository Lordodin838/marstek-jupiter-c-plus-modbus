# Marstek Jupiter C Plus — Modbus-Feldnotizen

*[English version: README.md](README.md)*

Lesende Modbus-Dokumentation für den **Marstek Jupiter C Plus** (800 W
Balkonspeicher, 4 PV-Strings), entstanden durch mehrtägiges Abtasten des
Registerraums an einem realen Gerät, jeder Wert gegen einen zweiten, unabhängigen
Datenpfad gegengeprüft.

Dieses Repository existiert, weil Marstek weder Registerdokumentation noch
Firmware-Changelogs veröffentlicht. Alles hier ist gemessen, nicht aus einem
Datenblatt abgeschrieben. Wo eine Deutung geraten ist, steht das dabei.

**Enthält drei Register, die in keiner öffentlichen Registerkarte stehen**, dazu
Korrekturen an den Grenzen des bekannten Datenblocks.

---

## Stand

| | |
|---|---|
| Gerät | Jupiter C Plus, 800 W (Gerätetyp-Register `0x0025` = 0) |
| Firmware | `142.37.213.110` (EMS 142 / BMS 37 / MPPT 213 / INV 110) |
| Zusätzlich geprüft auf | `138.37.213.110` — Registerkarte byteweise identisch |
| Anbindung | RS485 → Elfin EE11 → Modbus TCP, Unit 1, 115200 Bd |
| Funktionscodes | Nur FC3 (Holding Register lesen). **FC4 wird nicht unterstützt** — das Gerät antwortet mit Exception 1 |
| Umfang | Nur lesend. Nichts in diesem Repository schreibt ins Gerät |
| Stichprobe | Ein Gerät. Alles hier gilt als „an einem Exemplar bestätigt" |

---

## Neue Befunde

Diese drei sind der Grund für dieses Repository. Keiner davon steht in
[danielrahn/marstek-jupiter-c-plus](https://github.com/danielrahn/marstek-jupiter-c-plus),
das ansonsten die beste öffentliche Karte für dieses Gerät ist.

### `0x0011` — Fehlercode

Enthält denselben Fehlercode, den das Gerät über den Cloud-/MQTT-Pfad meldet,
**als Dezimalzahl des hexadezimalen Codes aus dem Handbuch.**

Diese Umrechnung ist der ganze Kniff, und sie ist leicht zu übersehen:

```
Register 0x0011 = 1062   ->   1062 dezimal = 0x426   ->   Handbuch: Fehler 426
```

Bestätigt, indem Modbus-Register und Cloud-Fehlercode während eines Störfalls
nebeneinander mitgeschrieben wurden: in 4 von 4 Abfragen, die ins Störfenster
fielen, stand das Register auf 1062, während die Cloud 426 meldete. Der eine
Fehlschlag war ein dreiminütiges Fenster zwischen zwei Abfragen — eine Lücke in
der Abtastung, kein Widerspruch.

Das ist deshalb wichtig, weil es einen **vollständig cloud-unabhängigen Alarmpfad**
eröffnet. Die Hersteller-App und die MQTT-Brücke hängen beide an Marsteks Servern,
dieses Register nicht.

`0` heißt: kein Fehler. Die Decodiertabelle steht in
[`docs/fault-codes.md`](docs/fault-codes.md).

### `0x1100`–`0x1105` — MAC-Adresse als ASCII

Sechs Register, je zwei ASCII-Zeichen, ergibt zwölf Hex-Ziffern:

```
0x1100  0x6134  "a4"
0x1101  0x6331  "c1"
0x1102  0x3338  "38"
0x1103  0x3966  "9f"
0x1104  0x3262  "2b"
0x1105  0x3765  "7e"
                 -> a4c1389f2b7e  ->  A4:C1:38:9F:2B:7E
```

Gegengeprüft gegen die Bluetooth-Adresse, die das Gerät aussendet. Zu beachten:
dort stehen die *Zeichen* `"a4"`, nicht der *Wert* `0xA4` — die Register enthalten
Text.

### `0x1200`–`0x1205` — Firmware des Kommunikationsmoduls, als ASCII

Gleiche Codierung, zwölf Ziffern, ein Build-Datumsstempel:

```
-> 202512040647   ->  04.12.2025, Build 0647
```

Das ist die Firmware des **Kommunikationsmoduls**, die getrennt von den vier
Versionsnummern in `0x001B`–`0x001F` geführt wird. Sie ändert sich bei einem
EMS-Update nicht — und ist damit die einzige Möglichkeit, überhaupt zu bemerken,
dass Marstek stillschweigend einen neuen Stand des Kommunikationsmoduls
ausgeliefert hat.

---

## Korrekturen an der bekannten Karte

Gemessen gegen die bisher veröffentlichten Grenzen:

| Bisherige Annahme | Befund |
|---|---|
| Datenblock reicht bis `0x0027` | **Nein — er endet bei `0x0025`.** `0x0026` und `0x0027` antworten nicht |
| Ab `0x0028` spiegelt das Gerät den Block | **Nein — `0x0028` und `0x0029` antworten nicht** |
| — | **`0x002A` existiert**, isoliert, mit toten Adressen davor und danach. Wert `1`, Bedeutung unbekannt |

Die früher dort gesehenen „Werte" auf `0x0026`/`0x0027` waren ein Artefakt eines
zu knappen Modbus-Timeouts am TCP-Gateway — verirrte Antworten, die in der
falschen Anfrage landen. Siehe [`docs/gateway.md`](docs/gateway.md); dieser
Fehlermodus ist die mit Abstand größte Quelle falscher Daten in so einem Aufbau,
und er meldet sich nicht von selbst.

Eine Grobsuche über den **gesamten 16-Bit-Adressraum** (zwei 8er-Stichproben je
256er-Seite, 244 Seiten außerhalb der bekannten Bereiche) fand keine weitere
antwortende Seite. Ehrliche Einschränkung: diese Methode findet *Blöcke*, keine
isolierten Einzelregister wie `0x002A`. In den Lücken können weitere Einzelgänger
stecken.

---

## Vollständige Registerkarte

Siehe [`docs/register-map.de.md`](docs/register-map.de.md) — jede Adresse, Inhalt,
Skalierung, und für jeden Eintrag eine ausdrückliche Sicherheitsangabe
(**bestätigt** / **plausibel** / **unbekannt**).

Zwei Register bleiben nach mehreren Tagen Beobachtung ungeklärt:

- **`0x0023`** — schmales Band, 36–39. Ausgeschlossen: Batteriestrom,
  Batteriespannung, SoC. Sprang über ein Firmware-Update hinweg von ~51 auf ~37,
  was gegen eine physikalische Messgröße spricht.
- **`0x0012`** — steht dauerhaft auf 0. Plausibel ein zweiter Alarm-/Warncode
  neben `0x0011`, unbewiesen.

Wer einen Jupiter C Plus hat: diese beiden gegen eine bekannte Last oder einen
Störfall zu halten würde sie klären. Issues willkommen.

---

## Inhalt

```
docs/register-map.de.md   Vollständige Registerkarte mit Sicherheitsangaben
docs/fault-codes.md       Fehlercodetabelle, hex ↔ dezimal
docs/gateway.de.md        RS485-Gateway und die drei Geräteeigenschaften,
                          die alles andere bestimmen
tools/regscan.py          Registerscanner und Differ, ohne Fremdbibliotheken
homeassistant/            Beispielpaket für Home Assistant (natives modbus:)
dumps/                    Referenz-Registerabzug, Firmware 142
```

### `tools/regscan.py`

Nur Standardbibliothek, kein pymodbus. Das Werkzeug baut die Modbus-TCP-Rahmen von
Hand, und zwar genau deshalb, damit es die Transaction-ID selbst prüfen kann — an
der patzen billige RS485-Ethernet-Wandler. Jeder Wert wird dreimal gelesen,
übernommen wird nur ein Mehrheitsentscheid; alles andere wird markiert statt still
verwendet.

```bash
python3 regscan.py --label vor-update         # Abzug anlegen
python3 regscan.py --sweep                    # zusätzlich 0x0000-0xFFFF absuchen
python3 regscan.py --diff vor-update.json     # gegen einen Abzug halten
```

**Vor jedem Firmware-Update einen Abzug ziehen.** Marstek liefert keine
Changelogs, und in der Marstek-Reihe haben sich Registeradressen zwischen
Gerätegenerationen nachweislich verschoben. Ein Vorher/Nachher-Vergleich ist die
einzige verlässliche Auskunft über *das eigene* Gerät.

### `homeassistant/`

Ein lauffähiges Paket für die native `modbus:`-Integration: alle bestätigten
Register als Sensoren, dazu ein Template-Sensor, der `0x0011` in Klartext
übersetzt.

Die Abfrageintervalle darin sind nicht willkürlich, sondern Primzahlen, und zwar
mit Absicht. [`docs/gateway.de.md`](docs/gateway.de.md#warum-primzahl-intervalle)
erklärt, warum sich das als wirksamer herausgestellt hat als jede andere einzelne
Einstellung.

---

## Dank

- **[danielrahn/marstek-jupiter-c-plus](https://github.com/danielrahn/marstek-jupiter-c-plus)**
  — die Ausgangskarte, von der diese Arbeit ausgeht, und die RS485-Hinweise zur
  Verkabelung (darunter, dass Pin 3 / +5 V offenbar nicht belegt ist).
- **[Issue #1 dort](https://github.com/danielrahn/marstek-jupiter-c-plus/issues/1)**
  — Zellspannungen `0x0020`/`0x0021` und das vermutete Temperaturregister
  `0x000E`, gemeldet von Lordodin838. Beides hier übernommen.
- **[retris83-ger/Marstek-Jupiter-C-Plus-Modbus-ESPHome](https://github.com/retris83-ger/Marstek-Jupiter-C-Plus-Modbus-ESPHome)**
  — ESPHome-Umsetzung für dasselbe Gerät.
- Die Marstek-Fäden im **photovoltaikforum.com**, wo der Fehlercode 426 einen
  eigenen Thread hat, treffend betitelt „Fehlercode 426, der Unbekannte".

## Lizenz

MIT — siehe [LICENSE](LICENSE).

## Haftungsausschluss

Messungen an einem einzigen Gerät. Modbus-Registeradressen können sich mit der
Firmware ändern. Hier schreibt nichts ins Gerät — wer darauf aufbaut und anfängt
zu schreiben, sollte vorher einen Abzug ziehen und sich darüber im Klaren sein,
dass ein 800-W-Wechselrichter am Hausnetz nichts ist, woran man blind
herumprobiert.
