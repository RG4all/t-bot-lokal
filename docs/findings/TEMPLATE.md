# TEMPLATE – Findings-Dokument

Kopieren nach `docs/findings/<TYP>-<NN>-<kebab-slug>.md` (TYP: SEC, BUG, PERF, CODE;
Nummer monoton steigend, nie wiederverwenden). Ein Dokument pro Befund; der Status im
Frontmatter ist die einzige Statuswahrheit — CHANGELOG und Reviews verlinken nur hierher.

```markdown
---
id: TYP-NN
title: Kurzbeschreibung des Befunds
finding: ScanKennungOderPromptNN
severity: LOW|MEDIUM|HIGH|CRITICAL
status: open            # open | in-review | fixed
fixed_in: null          # z.B. 2.4.20, erst bei "fixed" setzen
audit_ref: docs/archive/<audit>.md#<abschnitt>   # oder Reviews-Datei
tests: trading/tests/test_<name>.py
---

# TYP-NN – Titel

- **Finding:** `Kennung` (Quelle: Prompt/Audit-Abschnitt)
- **Status:** **Fixed / Resolved** in x.y.z  *(solange offen: "Offen, geplant für …")*
- **Release:** x.y.z · **Nachprüfung:** JJJJ-MM-TT (zuletzt x.y.z)
- **Ursprüngliche Einstufung:** SEVERITY – Kategorie; ggf. Relativierung nach Prüfung.
- **Fix-Commit:** `<hash>` – `commit-titel`

## Befund und Root Cause
Was war der Ausgangszustand, warum, und wie war die reale (nicht theoretische) Wirkung.
Falsche Prämissen des Ursprungsaudits ausdrücklich benennen.

## Fix und Umfang
Änderung, Konfigurationsabschnitte, bewusst NICHT geänderte Bereiche, API-/Migrationsfolgen.

## Regressionstests
Anzahl, Datei, abgedeckte Angriffs-/Randvektoren, Rot→Grün-Nachweis.

## Prüfgrenzen
Was bewusst nicht geprüft wurde (Laufzeit, Portability, externe Systeme) — Pflichtfeld,
damit ein Fix nicht als unbegrenzte Freigabe gelesen wird.
```

Regeln: keine Übernahme von Audit-Prosa (verlinken, nicht kopieren); nach Release den
Status im Frontmatter und in der Kopfzeile aktualisieren; keine Deep-Links auf
CHANGELOG-Anker.
