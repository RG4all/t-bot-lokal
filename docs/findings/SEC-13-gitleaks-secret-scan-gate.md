---
id: SEC-13
title: Secret-Scan-Gate (gitleaks) fail-closed und versionsgepinnt
finding: GitleaksSecretScanGate
severity: MEDIUM
status: fixed
fixed_in: 2.5.2
audit_ref: docs/security/CODE_REVIEW_2026-09-09.md
tests: tests/test_gitleaks_gate.sh
---

# SEC-13 – Secret-Scan-Gate (gitleaks) fail-closed und versionsgepinnt

- **Finding:** `GitleaksSecretScanGate` (CI-Härtung; Anlass: Secret-Scan-Ausfall
  und stille Fehlkonfigurationen in verwandten Workflows, 2026-09-14)
- **Status:** **Fixed** in 2.5.2
- **Release:** 2.5.2 · **Nachprüfung:** 2026-09-14
- **Ursprüngliche Einstufung:** MEDIUM – Security / CI-Supply-Chain
- **Fix-Commit:** siehe Release-PR zu 2.5.2

## Befund und Root Cause

Bis 2.5.1 enthielt der Quality-Gate von t-bot-lokal **keinen** Secret-Scan.
Versehentlich committete API-Keys, Tokens oder private Schlüssel wären nur
durch manuelle Reviews aufgefallen – und Reviews skalieren nicht mit der
Commit-Historie.

Zusätzlich zeigen verwandte Setups (gitleaks/gitleaks-action ohne
`GITLEAKS_VERSION`, Allowlists ohne `matchAll`) zwei stille Fehlerklassen:

1. **Unpinnte Scanner-Version:** `gitleaks-action` fällt ohne
   `GITLEAKS_VERSION` auf einen hardcodierten Default zurück (in v3.0.0:
   `8.24.3`). Regelwerk und Allowlist-Semantik ändern sich mit Releases;
   Gate-Ergebnisse hängen dann vom Veröffentlichungsdatum ab – dasselbe
   Anti-Pattern, das in 2.5.0 für Ruff (O1) und ShellCheck geschlossen wurde.
2. **Allowlist ODER statt UND (N-2):** Vor gitleaks 8.19 galten `paths` und
   `regexes` disjunktiv. Ein Dummy-Regex allowlistete tokenförmige Werte im
   *gesamten* Repo. Mit `matchAll = true` müssen beide Bedingungen greifen.
3. **Org-Lizenzfalle:** `gitleaks-action` verlangt für Organisation-Repos
   (`RG4all`) eine `GITLEAKS_LICENSE`. Fehlt sie, bricht der Job ab – oder
   Teams deaktivieren den Scan. Die freie CLI braucht keine Lizenz.

Root Cause ist nicht ein einzelnes geleaktes Secret in t-bot-lokal, sondern
das **fehlende, deterministische, fail-closed Gate** in CI.

## Fix und Umfang

| Komponente | Änderung |
|---|---|
| `scripts/run_gitleaks.sh` | Single Source of Truth: `GITLEAKS_VERSION=8.30.1`, SHA-256 pro Target (linux/darwin × x64/arm64), Download nur nach Integritätscheck, Tool-Cache (`RUNNER_TOOL_CACHE` / `XDG_CACHE_HOME`), `--self-test` (Dummy außerhalb freigegebener Pfade **muss** Exit 2 erzeugen), Historien-Scan mit `.gitleaks.toml`. Jeder Fehler → Exit 1, nie still grün. |
| `.gitleaks.toml` | `useDefault = true`; Allowlist-Vorlage nur kommentiert und mit `matchAll = true`-Pflicht dokumentiert. |
| `.github/workflows/quality.yml` | Neuer Job `secrets` (fetch-depth: 0, `run_gitleaks.sh --self-test`); wöchentlicher `schedule` (Mo 04:17 UTC); **kein** `gitleaks/gitleaks-action`. |
| `tests/test_gitleaks_gate.sh` | Statische Regressionen (Pin, SHA, Workflow-Hooks, kein Action-Lizenzpfad, executable, `--print-version`); optionaler Live-Lauf. |

Keine Runtime-Abhängigkeit, keine Settings-/Modelländerung, keine Migration.

## Regressionstests

- `tests/test_gitleaks_gate.sh` – Pin, Checksummen, Config-Policy, Workflow-Verdrahtung, Fail-closed-Marker, `--print-version`.
- CI-Job `Secret-Scan`: Selbsttest + voller Historien-Scan bei jedem Push/PR und wöchentlich.
- Rot-Kriterium Selbsttest: fehlt die Erkennung des Dummy-Leaks außerhalb freigegebener Pfade → Job rot (Gate wirkungslos).

## Prüfgrenzen

- Der Scan deckt die **Git-Historie und den Working Tree** der CI-Checkout-Kopie ab; Secrets in ungetrackten lokalen Dateien außerhalb der CI, in Docker-Volumes, Render-Env oder Browser-Speicher sind nicht Gegenstand.
- Entropy-/Regex-Heuristiken können False Positives erzeugen; Freigaben nur eng und mit `matchAll = true`.
- Binary-Download braucht Netzzugriff auf `github.com/gitleaks/gitleaks` Releases; ohne Netz schlägt der Job fail-closed fehl (kein Skip).
- Kein Ersatz für Secret-Rotation nach einem echten Leak und kein Penetrationstest der Exchange-/Render-Integration.
