# Reference dumps

## `regdump_fw142_sweep_20260918.txt`

Real output from a full scan with `--sweep` on firmware `142.37.213.110`,
kept verbatim. **The output is German** — that run was made with the original
German-language build of the scanner, before this repository existed. Glossary:

| German | English |
|---|---|
| Adresse | address |
| Wert | value |
| sicher — `ja` / `NEIN` | safe — `yes` / `NO` |
| Bereich / Hinweis | area / note |
| Datenblock | data block |
| Statusflags | status flags |
| Ohne Inhalt | without content (no response) |
| keine Antwort: timed out | no response: timed out |
| Grobsuche | coarse sweep |

**One value was changed:** registers `0x1100`–`0x1105` hold the device's MAC
address, and those six have been replaced with the documentation example
`A4:C1:38:9F:2B:7E`. Everything else is exactly as the tool wrote it.

### Why this run and not a cleaner one

Because it is instructive. This scan was taken at midday with the device
actively charging and Home Assistant polling the same bus, and **it is a
visibly noisy run**:

- **Seven values are flagged `NEIN`** — they did not survive the
  three-pass majority vote. Look at `0x0020` reading `3355`: a cell voltage
  of 3.355 V is plausible on its face. That is the whole danger. Only the
  repeat-and-vote catches it.
- **`0x0028` reports a value at all**, flagged `NEIN`, with the note
  `keine Antwort: timed out`. That register does not exist. This is a stray
  response landing in the wrong slot — the exact mechanism described in
  [`../docs/gateway.md`](../docs/gateway.md).
- **`0x0021`–`0x0027` show as "without content"**, meaning `0x0021`
  (cell voltage min), `0x0022` (screen version) and `0x0025` (device type)
  dropped out of this run entirely. They exist; they just did not answer
  during the boundary search here.

A night-time run with Home Assistant's polling paused gives a clean sheet.
**Take your reference dumps at night.** The point of keeping this one in the
repository is to show what an unreliable run looks like, so you can recognise
one when the values look perfectly reasonable.

### What is genuinely settled by this run

`0x1100`–`0x1105` and `0x1200`–`0x1205` came back `ja` on all three passes,
and the coarse sweep across the remaining 244 pages of the address space found
nothing further.

Note that both ASCII blocks are labelled `Statusflags` — that is only the name
of the scan range they fell inside (`0x1000`–`0x13FF`), not a claim about what
they contain.

## Taking your own

```bash
python3 ../tools/regscan.py --host <your-gateway> --label before-update
# ... update firmware ...
python3 ../tools/regscan.py --host <your-gateway> --label after-update \
    --diff regdump_before-update_<stamp>.json
```

Do it before every firmware update. It is the only way to find out what
changed on **your** device, because Marstek will not tell you.
