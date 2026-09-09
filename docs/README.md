# Dokumentation – Index und Pflege

Einstieg ins Projekt: [README im Repo-Stamm](../README.md). Diese Seite ist der **vollständige Index** aller Dokumente, dazu die Pflege- und Benennungsregeln. Beiträge und Patches: [CONTRIBUTING.md](../CONTRIBUTING.md).

## Handbuch & Betrieb

| Dokument | Inhalt |
|---|---|
| [manual/MANUAL.md](manual/MANUAL.md) | Benutzer- und Indikatorhandbuch; wird von der App unter `/help/` gerendert (Pfad in `trading/views.py` hinterlegt). |
| [manual/BACKTESTING.md](manual/BACKTESTING.md) | Backtesting-Kapitel: Annahmen, Formeln, Grenzen der Ergebnisse. |
| [operations/LOCAL_DEVELOPMENT.md](operations/LOCAL_DEVELOPMENT.md) | Lokales Setup (Docker und ohne), Passphrase-Matrix, Hardwarediagnose, Render-Free-Simulation, Graceful Degradation, Render-Deployment, Qualitätssicherung. |
| [operations/FAQ.md](operations/FAQ.md) | Häufige Fragen, Troubleshooting, Secret-Rotation. |
| [operations/REMOTE_ACCESS_TAILSCALE.md](operations/REMOTE_ACCESS_TAILSCALE.md) | Fernzugriff ohne öffentliche Ports über Tailscale (DS-Lite-Szenario). |
| [operations/CADDY_PROXY.md](operations/CADDY_PROXY.md) | LAN-Zugriff über Caddy als Reverse Proxy im Docker-Stack. |

## Sicherheit & Reviews

| Dokument | Status | Inhalt |
|---|---|---|
| [security/SECURITY_REVIEW_2.4.4.md](security/SECURITY_REVIEW_2.4.4.md) | **verbindlich, aktuell** | Befunde, Prüfgrenzen und Release-Nachweis des Security-Reviews. |
| [security/CODE_REVIEW_2026-09-09.md](security/CODE_REVIEW_2026-09-09.md) | Review 2026-09-09 | Umfassender Code-Review (nach Kritikalität priorisiert): Bot-Lifecycle-Race, Fail-open-Restart, Cache-Leak, Dependency-/CI-Hygiene, Performance und Umsetzungsreihenfolge. |
| [CHANGELOG.md](CHANGELOG.md) | gepflegt | Release-Historie; einzige Quelle für „Fixed in x.y.z“-Kontext. |

### Findings

Ein Dokument pro abgeschlossenem Audit-/Review-Befund. Der Status im Dokumentenkopf ist der Umsetzungsstand; Pflichtstruktur siehe [findings/TEMPLATE.md](findings/TEMPLATE.md).

| Finding | Befund | Status laut Dokument |
|---|---|---|
| [SEC-06](findings/SEC-06-content-type-nosniff.md) | X-Content-Type-Options: nosniff | Fixed |
| [SEC-07](findings/SEC-07-session-lifetime-invalidation.md) | Session-Lifetime und Logout-Invalidierung | Fixed |
| [SEC-08](findings/SEC-08-cache-control-api.md) | Cache-Control für API-Endpunkte | Fixed |
| [SEC-09](findings/SEC-09-permissions-policy.md) | Permissions-Policy-Header | Fixed |
| [SEC-10](findings/SEC-10-information-disclosure.md) | Fehlermeldungen / Information Disclosure | Fixed |
| [SEC-12](findings/SEC-12-docker-default-passwords.md) | Standard-Passwörter im Compose-Setup | Fixed |
| [BUG-12](findings/BUG-12-race-condition-bot-start-stop.md) | Race Condition Bot-Start/Stop | Fixed |
| [BUG-14](findings/BUG-14-csv-echo-true-stream.md) | CSV-Echo-Adapter im Stream-Export | Fixed |
| [PERF-17](findings/PERF-17-db-trim-batch-delete.md) | DataLog-Trim als Batch-Delete | Fixed |
| [PERF-21](findings/PERF-21-info-api-db-aggregation.md) | info_api-Kennzahlen per DB-Aggregation | Fixed |
| [CODE-18](findings/CODE-18-indicator-dedup.md) | Indikator-Deduplizierung Bot/Backtest | Fixed |
| [CODE-19](findings/CODE-19-view-type-hints.md) | Typannotation und Doku der Views | Fixed |
| [CODE-20](findings/CODE-20-module-exports.md) | Explizite Modul-Exports (`__all__`) | Fixed |

## Architektur-Entscheidungen

| Dokument | Inhalt |
|---|---|
| [adr/ADR-0001-backtesting-worker-isolation.md](adr/ADR-0001-backtesting-worker-isolation.md) | Machbarkeitsstudie und Entscheidung zur Worker-Isolation (Celery/Redis, Render-Free-Grenzen). |

## Qualitätssicherung & Konventionen

- Konventionen, Findings-Vorlage und Peer-Review-/Patch-Workflow: [CONTRIBUTING.md](../CONTRIBUTING.md)
- Patch-Eingang für Review-Vorschläge: [`patches/`](../patches/README.md)
- Dokument-Wächter (Links, Anker, Orphans, Namensregeln): `python3 scripts/check_docs.py`
- Automatischer Qualitäts-Gate bei jedem Push/PR (Doku-Wächter, Django-Checks/-Tests, Ruff, Shell-Suite, ShellCheck, `pip check`): `.github/workflows/quality.yml`

## Archiv

Überholte Dokumente sind Lesezugriff, werden nicht nachgezogen und tragen ein Archivbanner im Kopf:

| Dokument | Ursprünglicher Zweck |
|---|---|
| [archive/SECURITY_AUDIT_2026-09-07.md](archive/SECURITY_AUDIT_2026-09-07.md) | Ursprungs-Audit aller Befunde (Grundlage der Findings und Archive-Prompts). |
| [archive/ARENA_AI_PROMPTS_2026-09-07.md](archive/ARENA_AI_PROMPTS_2026-09-07.md) | PR-Promptsammlung aus dem Audit. |
| [archive/PEER_REVIEW_BUILD_2026-08-21.md](archive/PEER_REVIEW_BUILD_2026-08-21.md) | Selbst-Review der Build-/Install-Ueberarbeitung. |
| [archive/PEER_REVIEW_LOCAL_SETUP_2.3.0.md](archive/PEER_REVIEW_LOCAL_SETUP_2.3.0.md) | Architektur-Review des lokalen Setups. |
| [archive/PEER_REVIEW_BACKTESTING_CHATGPT_2026-08.md](archive/PEER_REVIEW_BACKTESTING_CHATGPT_2026-08.md) | Externes KI-Review der Backtesting-Studie. |
| [archive/AUFRUMUNGSPLAN_2026-09.md](archive/AUFRUMUNGSPLAN_2026-09.md) | Plan und Entscheidungsgrundlagen dieser Umstrukturierung. |

## Pflegeregeln (Kurzfassung)

1. **Dateinamen:** Findings/ADRs als `TYP-NN-kebab-slug.md`; Dauerdokumente als `SCREAMING_SNAKE.md`; Ausnahmen nur `README.md`/`CHANGELOG.md`. Archivname = Originalname + `_YYYY-MM-DD`.
2. **Ein Thema, ein Ort:** Setup nur in `operations/LOCAL_DEVELOPMENT.md`, Status nur im Findings-Dokument, Historie nur im CHANGELOG. Verlinken statt kopieren.
3. **Keine Versionszahlen in Fließtext** außerhalb von CHANGELOG/ARCHIV; Single Source of Truth ist die `VERSION`-Datei im Repo-Stamm.
4. **Keine Deep-Links auf Release-Anker** (GitHub-Slugs sind nicht portabel): immer auf Dateien verlinken, innerhalb einer Datei auf eigene Überschriften.
5. **Neues Review ersetzt altes:** altes Dokument im selben PR ins `archive/` mit Banner.
6. Vor jedem Merge: `python3 scripts/check_docs.py` muss fehlerfrei sein (verlinkt in `patches/`-Checkliste und CI).
