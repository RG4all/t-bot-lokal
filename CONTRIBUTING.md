# Beitragen – Konventionen und Abläufe

Struktur-Leitplan des Repositories. Änderungen an der Doku-Struktur nur mit Begründung
im PR; die Umstrukturierung 2026-09 ist in
[docs/archive/AUFRUMUNGSPLAN_2026-09.md](docs/archive/AUFRUMUNGSPLAN_2026-09.md)
dokumentiert.

## Verzeichnislandkarte

| Pfad | Inhalt | Änderungsrhythmus |
|---|---|---|
| `README.md` | Einstieg: Setup-Ecke, Sicherheitskurzfassung, Linkkarte | bei Nutzänderungen |
| `docs/README.md` | **Index** aller Dokumente + Pflegeregeln | bei jedem neuen Dokument |
| `docs/manual/` | Nutzerdoku; `MANUAL.md` wird von `/help/` gerendert (Pfad in `trading/views.py`) | bei Feature-Änderungen |
| `docs/operations/` | Betrieb: Setup, FAQ, Tailscale, Caddy | bei Infrastruktur-Änderungen |
| `docs/findings/` | Ein Dokument pro Audit-Befund; `TEMPLATE.md` ist Pflichtvorlage | bei Befund-/Statusänderungen |
| `docs/security/` | Aktuelles Security-Review (einzige "verbindliche" Review-Datei) | pro Review-Zyklus |
| `docs/adr/` | Architektur-Entscheidungen (`ADR-NNNN-…md`) | bei Entscheidungen |
| `docs/archive/` | Überholte Dokumente, nur Lesezugriff, Banner Pflicht | nur beim Archivieren |
| `docs/CHANGELOG.md` | Release-Historie (Keep a Changelog) | jeder Release-PR |
| `patches/` | Peer-Review-Patch-Eingang (`inbox/`→`accepted/`/`done/`) | kontinuierlich |
| `VERSION` | Einzige Versionsquelle (Settings, UI, `/health/`) | nur Release-PR |
| `.github/workflows/quality.yml` | Aktiver CI-Qualitäts-Gate (Doku-Wächter, Django-Tests, Shell, Ruff) | bei CI-Änderungen |

## Dateinamen

- Findings/ADRs: `TYP-NN-kebab-slug.md` (TYP ∈ SEC, BUG, PERF, CODE, ADR; Nummern nie
  wiederverwenden; der Slug beschreibt den **Inhalt**, nicht den Auftrag).
- Dauerdokumente: `SCREAMING_SNAKE.md`. Ausnahmen: `README.md`, `CHANGELOG.md`.
- Archiv: Originalname + `_YYYY-MM-DD`.
- Verboten: gemischte Konventionen in einem Namen (`Peer-Review_Backtesting-chatGPT`),
  Personennamen/Tools im Pfad (Zugehörigkeit steht im Dokumentenkopf),
  Klein-/Großbuchstaben-Mix, `manual-…-manual`-Doppelungen.
- `python3 scripts/check_docs.py` erzwingt diese Regeln als CI-Gate.

## Anti-Duplikat-Regeln

1. **Ein Thema, ein Ort.** Setup nur in `operations/LOCAL_DEVELOPMENT.md`; Befunddetails
   nur im Findings-Dokument; Release-Kontext nur im CHANGELOG. Verlinken statt kopieren.
2. **Keine Versionszahlen in Fließtext** (außer CHANGELOG/Archiv). `VERSION` ist die
   einzige Quelle; die App zeigt sie selbst.
3. **Keine Deep-Links auf CHANGELOG-Anker** (`…#241--…` ist ein GitHub-Artefakt und
   bricht in lokalen Viewern). Auf Dateien verlinken; Anker nur innerhalb derselben Datei.
4. **Statuspflege zentral:** Findings-Frontmatter `status:`/`fixed_in:` ist die einzige
   Statuswahrheit; README/Index wiederholen keine Befundtexte.
5. **Reviews ersetzen, nicht anhäufen:** Erscheint ein neues Review in `docs/security/`,
   wandert das alte im selben PR mit Banner nach `docs/archive/`.
6. **KI-/Agentur-Arbeitsdokumente** (Prompts, Chat-Exporte) beginnen in `patches/inbox/`
   oder `docs/archive/`, nie im aktiven `docs/`-Bestand.

## Review-Ablauf pro PR

1. Zweck und betroffene Pfade im PR-Text; bei Nutzänderung: CHANGELOG-Abschnitt unter
   `Unreleased` pflegen, `VERSION` nur im Release-PR erhöhen.
2. Prüfungen lokal und in CI (siehe QA-Befehle in
   [docs/operations/LOCAL_DEVELOPMENT.md](docs/operations/LOCAL_DEVELOPMENT.md#12-qualitätssicherung)):
   Django-Tests, `tests/run_tests.sh`, `shellcheck`, `ruff check .`, `python manage.py check`,
   `makemigrations --check --dry-run`, `python3 scripts/check_docs.py`.
3. Externe Vorschläge laufen über [`patches/README.md`](patches/README.md).
4. Reine Umzüge: `git mv` in separatem Rename-Commit ohne Textänderungen, Linkpflege im
   Folge-Commit (hält `git log --follow` und GitHub-Rename-Erkennung intakt).
5. **CI-Dateien:** Der Qualitäts-Gate liegt aktiv unter `.github/workflows/quality.yml` und läuft bei jedem Push/PR automatisch (Prüfumfang: Doku-Wächter, Django-Checks/-Tests, Ruff, Shell-Suite, ShellCheck, `pip check`).
6. Nach einem Merge: betroffene Findings-Status aktualisieren; falls ein Dokument seinen
   Ort gewechselt hat, Index (`docs/README.md`) mitpflegen — der CI-Orphan-Check meldet
   sonst „verwaist“.

## Regelmäßige Audits

- **Quartalsweise:** `python3 scripts/check_docs.py --` Hinweisliste durchgehen
  (Duplikatsabsätze, verwaiste Dokumente); alle Findings mit `status: open` prüfen;
  abgelaufene Render-/Dependency-Hinweise aktualisieren.
- **Pro Zyklus (mind. halbjährlich):** statischer Scan der eigenen Module
  (`bandit`, `ruff`, `mypy trading/views.py`) plus `pip-audit -r requirements.txt`;
  Ergebnis als neues `SECURITY_REVIEW_<version>.md` in `docs/security/`, altes archivieren.
- **Jährlich:** `docs/archive/` und `patches/accepted|done/` aufräumen; Konventionen in
  dieser Datei auf Reibungspunkte prüfen und nachschärfen.
