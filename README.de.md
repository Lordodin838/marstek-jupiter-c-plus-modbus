# Marstek Jupiter C Plus — Modbus-Feldnotizen

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Lordodin838/marstek-jupiter-c-plus-modbus?label=Lizenz" alt="Lizenz"></a>
  <img src="https://img.shields.io/badge/Register-65%20von%2065536%20belegt-blue" alt="65 von 65536 Adressen belegt">
  <img src="https://img.shields.io/badge/Firmware-142.37.213.110-informational" alt="Firmware 142.37.213.110">
  <img src="https://img.shields.io/badge/Zugriff-nur%20lesend-brightgreen" alt="Nur lesend">
  <a href="https://github.com/Lordodin838/ha-marstek-jupiter-c-plus"><img src="https://img.shields.io/badge/Home%20Assistant-Integration-41BDF5?logo=homeassistant&logoColor=white" alt="Home-Assistant-Integration"></a>
</p>

<p align="center">
  <a href="README.md">🇬🇧 English</a> · <b>🇩🇪 Deutsch</b>
</p>

Lesende Modbus-Dokumentation für den **Marstek Jupiter C Plus** (800 W
Balkonspeicher, 4 PV-Strings), entstanden durch mehrtägiges Abtasten des
Registerraums an einem realen Gerät, jeder Wert gegen einen zweiten, unabhängigen
Datenpfad gegengeprüft.

Dieses Repository existiert, weil Marstek weder Registerdokumentation noch
Firmware-Changelogs veröffentlicht. Alles hier ist gemessen, nicht aus einem
Datenblatt abgeschrieben. Wo eine Deutung geraten ist, steht das dabei.

**Enthält drei Register, die in keiner öffentlichen Registerkarte stehen**, dazu
Korrekturen an den Grenzen des bekannten Datenblocks.

> **Nur die Sensoren in Home Assistant gesucht?**
> Dann nimm die fertige Integration:
> **[ha-marstek-jupiter-c-plus](https://github.com/Lordodin838/ha-marstek-jupiter-c-plus)** — über HACS
> installierbar, Einrichtung in der Oberfläche, kein YAML. Dieses Repository ist
> die Dokumentation dahinter.

## Inhaltsverzeichnis

- [Stand](#stand) — was gemessen wurde, an welchem Gerät und welcher Firmware
- [Neue Befunde](#neue-befunde) — drei Register und eine zweite Unit-Adresse, die in keiner anderen Karte stehen
- [Korrekturen an der bekannten Karte](#korrekturen-an-der-bekannten-karte)
- [Vollständige Registerkarte](#vollständige-registerkarte) — jede Adresse mit Sicherheitsangabe
- [Inhalt](#inhalt) — Scanner, Home-Assistant-Package, Dumps
- [Dank](#dank)

---

## Stand

| | |
|---|---|
| Gerät | Jupiter C Plus, 800 W (Gerätetyp-Register `0x0025` = 0) |
| Firmware | `142.37.213.110` (EMS 142 / BMS 37 / MPPT 213 / INV 110) |
| Zusätzlich geprüft auf | `138.37.213.110` — Registerkarte byteweise identisch |
| Anbindung | RS485 → Elfin EE11 → Modbus TCP, Unit 1 (antwortet auch auf **11**, siehe unten), 115200 Bd |
| Funktionscodes | Nur FC3 (Holding Register lesen). **FC1, FC2 und FC4 werden nicht unterstützt** — Exception 1, es gibt also weder Coils noch Discrete Inputs. FC17 und FC43 ignoriert das Gerät ganz — auch mit dem Gateway transparent, es gibt also keine Gerätekennung im Klartext |
| Adressraum | **Vollständig abgesucht**: alle 65 536 Adressen einzeln, 65 davon belegt ([Vollsuche](#korrekturen-an-der-bekannten-karte), 30.09.2026) |
| Umfang | Nur lesend. Nichts in diesem Repository schreibt ins Gerät |
| Stichprobe | Ein Gerät. Alles hier gilt als „an einem Exemplar bestätigt" |
| Jupiter E | Marsteks eigene Tabelle deckt C und E gemeinsam ab (Gerätetyp 3–5); die Karte dürfte dort ebenso gelten — an einem E wurde nicht gemessen |

---

## Neue Befunde

Die ersten drei sind der Grund für dieses Repository; der vierte kam bei der
Protokollabfrage am 29. September 2026 heraus. Keiner davon steht in
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

### Unit-ID 11 — dasselbe Gerät unter einer zweiten Adresse

Der Jupiter antwortet auf **Unit 1 und Unit 11**. Ein Durchlauf über alle
Unit-IDs von 1 bis 247 hat genau diese beiden gefunden, mit Unit 1 als Kontrolle
vor und nach dem Durchlauf. Dieselben Blöcke von beiden nebeneinander gelesen:

| | Unit 1 | Unit 11 |
|---|---|---|
| MAC `0x1100`–`0x1105` | die MAC des Geräts | identisch, alle 12 Zeichen |
| Kommunikationsmodul `0x1200`–`0x1205` | `202512040647` | `202512040647` |
| Versionen `0x001B`–`0x001F` | 11 142 110 213 37 | identisch |
| Funktionscodes 1, 2, 4, 17, 43 | wie oben unter Stand | identisch |

Die Messwerte unterschieden sich nur so, wie zwei Sekunden auseinander gelesene
Messwerte das tun. Es ist ein Gerät mit zwei Namen, keine zweite Komponente.
Warum 11, und ob das mit der schreibbaren Geräte-ID in `0x4004` zusammenhängt,
ist nicht bekannt — das herauszufinden hieße, ins Gerät zu schreiben.

**Warum das zählt: kein anderes Gerät mit Adresse 11 an denselben RS485-Bus
hängen.** Zwei Geräte, die auf dieselbe Anfrage antworten, überlagern ihre
Telegramme — und heraus kommen genau die falschen Werte, bei denen man dann am
Gateway nach dem Fehler sucht.

---

## Korrekturen an der bekannten Karte

Gemessen gegen die bisher veröffentlichten Grenzen:

| Bisherige Annahme | Befund |
|---|---|
| Datenblock reicht bis `0x0027` | **Stimmt, und er reicht noch weiter: bis `0x002A`.** 42 Register ohne Lücke |
| Ab `0x0028` spiegelt das Gerät den Block | **Nein.** `0x0028`–`0x002A` sind eigene Register mit Werten `1`, `0`, `1` |
| — | **`0x0026`–`0x0029` antworten erst nach ~3,6 s.** Jedes andere Register braucht ~0,2 s |

Der dritte Punkt erklärt, warum hier bis zum 29. September das Gegenteil stand. Ein
RS485-Gateway mit automatischem Modbus-Timeout gibt vor 3,6 s auf; die vier
Register sehen dann tot aus, und `0x002A` dahinter wirkt wie ein isolierter
Einzelgänger. Erst mit festen 5000 ms am Gateway kamen die Antworten durch —
dreimal gemessen, jedes Mal nach derselben Zeit. Was die vier bedeuten, ist
offen; [die Registerkarte](docs/register-map.de.md#die-langsamen-register-0x00260x0029)
hat die Werte und was für einen Client daraus folgt.

**Korrektur.** Eine frühere Fassung dieses Abschnitts erklärte, der Datenblock
ende bei `0x0025`, `0x0026`–`0x0029` existierten nicht, und `0x002A` sei
isoliert. Das war ein Messfehler durch den zu kurzen Gateway-Timeout, kein
Befund über das Gerät.

Die verirrten Werte, die ältere Karten und der Abzug vom 18. September auf
diesen Adressen zeigten (`800` auf `0x0028`), bleiben trotzdem falsch: sie sind
die verspätete Antwort, die bei der *nächsten* Anfrage landet. Siehe
[`docs/gateway.de.md`](docs/gateway.de.md); dieser Fehlermodus ist die mit
Abstand größte Quelle falscher Daten in so einem Aufbau, und er meldet sich nicht
von selbst.

**Nichts übersehen.** Am 30. September 2026 wurden alle 65 536 Adressen
einzeln abgefragt, gut sechs Stunden lang. Belegt sind genau 65:
`0x0001`–`0x002A`, `0x1000`–`0x100A`, `0x1100`–`0x1105` und `0x1200`–`0x1205`.
Alle übrigen antworten mit „gibt es nicht". Die frühere Grobsuche hatte nur
Blöcke finden können, keine Einzelgänger — jetzt ist auch das ausgeschlossen.

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
tools/vollsuche.py        Alle 65 536 Adressen einzeln, fortsetzbar
tools/fakejupiter.py      Simuliertes Gerät zum Testen der Werkzeuge
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
python3 regscan.py --rtu                      # Modbus RTU over TCP
python3 regscan.py --probe                    # worauf antwortet es sonst?
python3 vollsuche.py --host 192.168.1.50      # jede Adresse einzeln, ~6 h
```

Für `0x0026`–`0x0029` braucht es am Gateway einen festen Modbus-Timeout von
mindestens 4500 ms und `--timeout 6`; sonst stehen die vier als tot im Abzug.

`--probe` fragt das Gerät nach dem, was ein Registerscan nicht erreicht: andere
Funktionscodes (Coils und Discrete Inputs sind ein **eigener Adressraum** — dass
Holding Register belegt sind, sagt darüber nichts), die beiden Aufrufe zur
Gerätekennung, die Herstellerangaben im Klartext liefern, und welche Unit-IDs
überhaupt antworten. Durchweg nur lesend; FC8 bleibt bewusst draußen, weil zu
seinen Unterfunktionen der Neustart des Kommunikationsmoduls gehört.

Jeder Lauf zählt mit, *warum* Antworten verworfen wurden — eine Änderung am
Aufbau lässt sich so an einer Zahl beurteilen statt am Gefühl; `--benchmark`
erledigt diese Messung in Minuten statt Stunden. `--rtu` spricht rohe
RTU-Telegramme statt Modbus TCP und nimmt dem Gateway die Protokollumsetzung ab
— eine Option für den Fall, dass diese Zähler steigen, kein Standard. Was das
abfängt und was nicht, und ein gemessener Ausgangswert, stehen in
[`docs/gateway.de.md`](docs/gateway.de.md#falls-doch-nötig-die-umsetzung-vom-gateway-wegnehmen).

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
- **[stevedee78/Marstek-Jupiter-E-Modbus-ESPhome](https://github.com/stevedee78/Marstek-Jupiter-E-Modbus-ESPhome)**
  — enthält ein Foto von Marsteks eigener Registertabelle, die Jupiter C und E
  gemeinsam abdeckt. Von dort stammen die Bedeutungen von `0x1009`, `0x100A`, die
  Gerätetyp-Codes und die Schreibregister `0x4000`–`0x4004` in diesem Repository.
- **[h6s/Marstek-Jupiter-E_ioBroker](https://github.com/h6s/Marstek-Jupiter-E_ioBroker)**
  — ESPHome-Konfiguration für den Jupiter E, gleiche Adressen.
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
