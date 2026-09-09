# Review-Kopf für Patch-Vorschläge

Kopieren nach `patches/inbox/<JJJJ-MM-TT>-<slug>.md`. Pflichtfelder ausfuellen;
unvollstaendige Reviews landen zurueck an den Einsender.

```markdown
# Review: <Kurztitel>

- **Datum:** JJJJ-MM-TT
- **Einsender:** <Name/Rolle>
- **Bezug:** Commit/Version oder Findings-Dokument (`docs/findings/...`)
- **Patch-Serie:** `0001-….patch` (git format-patch), PR-Link optional

## Scope
Welche Dateien/Module sind betroffen, was ist ausdruecklich NICHT Teil des Vorschlags.

## Befund / Motivation
Welches Problem loest der Patch; Referenz auf Audit-/Finding-Nummer, wenn vorhanden.

## Teststatus des Einsenders
- [ ] `bash tests/run_tests.sh` gruen
- [ ] `python manage.py test --noinput` gruen (AUTOSTART_BOTS=False DEBUG=True RENDER=False)
- [ ] `python3 scripts/check_docs.py` gruen (nur noetig bei Doku-Aenderungen)
- [ ] Neue/angepasste Regressionstests beschrieben (Datei + Anzahl)

## Maintainer-Entscheidung (nachtraeglich)
- Ergebnis: uebernommen | teilweise | abgelehnt
- Begruendung / Pruefgrenzen: …
- Follow-up-Findings: `docs/findings/…`
```
