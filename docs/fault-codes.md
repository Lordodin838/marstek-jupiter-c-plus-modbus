# Fault codes — Jupiter C Plus

*Bilingual: English and German in one table.*

## The conversion

Register **`0x0011`** holds the **decimal value** of the code the manual prints in
**hexadecimal**. Home Assistant, a Modbus browser, or anything else will show you
the decimal. Convert before looking it up:

```
register value 1062   ->   hex 0x426   ->   manual code 426
```

Python: `'{:X}'.format(value)` · Jinja: `{{ '%X' | format(value) }}`

`0` means no fault.

The same codes arrive over the cloud/MQTT path already in hex — which is exactly
why the mismatch is easy to miss when comparing the two sources.

## Table

| Code (hex) | Register reads (dec) | English | Deutsch |
|---|---|---|---|
| `404` | `1028` | Grid-side overheat protection | Netzseitiger Überhitzungsschutz |
| `406` | `1030` | Grid overvoltage | Netz-Überspannung |
| `408` | `1032` | Grid undervoltage | Netz-Unterspannung |
| `409` | `1033` | Grid overfrequency | Netz-Überfrequenz |
| `410` | `1040` | Grid underfrequency | Netz-Unterfrequenz |
| `414` | `1044` | Grid islanding detected | Netz-Inselerkennung |
| `415` | `1045` | Grid overvoltage | Netz-Überspannung |
| `418` | `1048` | Device fault | Gerätefehler |
| `419` | `1049` | Device fault | Gerätefehler |
| `422` | `1058` | Grid overcurrent | Netz-Überstrom |
| `426` | `1062` | **Undocumented** — not in the manual, which jumps from 422 to 440 | **Undokumentiert** — steht nicht im Handbuch, das von 422 auf 440 springt |
| `440` | `1088` | Battery overvoltage | Batterie-Überspannung |
| `441` | `1089` | Battery overcurrent | Batterie-Überstrom |
| `442` | `1090` | Battery undervoltage | Batterie-Unterspannung |
| `443` | `1091` | Current reversal | Stromumkehr |
| `444` | `1092` | Start-up voltage too low | Startspannung zu niedrig |
| `445` | `1093` | PV overheat protection | PV-Überhitzungsschutz |
| `446` | `1094` | PV1 overcurrent | PV1-Überstrom |
| `447` | `1095` | PV2 overcurrent | PV2-Überstrom |
| `448` | `1096` | PV3 overcurrent | PV3-Überstrom |
| `449` | `1097` | PV4 overcurrent | PV4-Überstrom |
| `450` | `1104` | PV negative pole miswired | PV-Minuspol falsch verdrahtet |
| `451` | `1105` | PE earthing anomaly | PE-Erdungsanomalie |
| `452` | `1106` | PE earthing anomaly | PE-Erdungsanomalie |
| `453` | `1107` | Battery overvoltage | Batterie-Überspannung |
| `454` | `1108` | Current reversal | Stromumkehr |
| `4C0` | `1216` | Slave communication error | Slave-Kommunikationsfehler |
| `4C1` | `1217` | Slave communication error | Slave-Kommunikationsfehler |
| `4C2` | `1218` | Temperature limit reached | Temperaturgrenze erreicht |
| `4C3` | `1219` | Temperature limit reached | Temperaturgrenze erreicht |
| `4C4` | `1220` | Temperature limit reached | Temperaturgrenze erreicht |
| `530` | `1328` | Battery charge too low | Batterieladung zu gering |
| `547` | `1351` | Battery charge too low | Batterieladung zu gering |
| `548` | `1352` | Battery charge too low | Batterieladung zu gering |
| `5C0` | `1472` | CT connection error | CT-Verbindungsfehler |
| `5C1` | `1473` | Phase sequence detection failed | Phasenfolge-Erkennung fehlgeschlagen |
| `5C2` | `1474` | Wi-Fi signal anomaly | WLAN-Signalanomalie |
| `5C3` | `1475` | Bluetooth status anomaly | Bluetooth-Status auffällig |
| `5C4` | `1476` | OTA update failed | OTA-Update fehlgeschlagen |
| `5C5` | `1477` | OTA update failed | OTA-Update fehlgeschlagen |
| `5C6` | `1478` | OTA update failed | OTA-Update fehlgeschlagen |
| `5C7` | `1479` | Network anomaly | Netzwerkanomalie |
| `5C8` | `1480` | Network anomaly | Netzwerkanomalie |
| `5C9` | `1481` | Network anomaly | Netzwerkanomalie |
| `5CA` | `1482` | Network anomaly | Netzwerkanomalie |
| `5CB` | `1483` | Network anomaly | Netzwerkanomalie |

## Notes

**426 is real but undocumented.** The manual has a gap between 422 and 440, and
this device has emitted 426 repeatedly. It is discussed in the German
photovoltaikforum under a thread titled "Fehlercode 426, der Unbekannte" — nobody
there has pinned it down either. If you see it, it appears to be transient: it
cleared on its own here without intervention.

**Codes not in this table** exist. The table is what the manual lists plus 426.
Anything else, report it — an unmapped code is more interesting than a mapped one.

**`0x0012`** may hold a second code of the same kind. It has stood at 0 throughout
observation here, so there has been nothing to compare. If your device ever shows
a non-zero value there during a fault, that would settle it.
