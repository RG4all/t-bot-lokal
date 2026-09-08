# Aufräum- und Reorganisierungsplan – t-bot-lokal

> **Archiv-Hinweis nach Umsetzung 2026-09-09:** Alle Phasen dieses Plans sind umgesetzt;
> das Dokument bleibt als Entscheidungsgrundlagen-Archiv. Verbindliche Struktur heute: [docs/README.md](../README.md).
**Datum:** 2026-09-08 · **Stand:** `0607b77` · Version 2.4.19 · **Branch:** `arena/01a0835f-t-bot-lokal`
**Methodik:** Vollständiger Repo-Scan (192 Dateien, 29 Markdown-Dokumente), automatisierter Link-/Anchor-Check (193 relative Links, 38 Anker-Links), Referenzgraph-Analyse, Code-Kopplungsprüfung (`grep` über Py/SH/YML/Dockerfile).

---

## 0. Executive Summary

Das Repo ist **inhaltlich reicher, aber strukturell ein Single-Commit-Upload**: 29 Markdown-Dateien liegen flach in `docs/`, vermischt aus User-Manual, Betriebsanleitungen, 15 Finding-Tickets, zwei Security-Audits, vier Peer-Reviews und einem 72 KB großen Prompt-Arbeitsdokument. Es gibt **keine Datei-LINKS ins Leere** (das ist gut!), aber **einen kaputten Anchor**, **vier verwaiste Dokumente**, **einen echten Doppel-README** und **eine Code-Kopplung an `docs/MANUAL.md`**, die bei jeder Verschiebung mitgezogen werden muss.

**Kernaussagen des Plans:**

1. **Konsolidieren:** `docs/README.md` (1091 Wörter) ist zu ~41 % Duplikat des Root-`README.md`. Root-README wird zum einzigen Einstiegspunkt, `docs/README.md` wird zum reinen Index umgeschrieben.
2. **Sortieren:** `docs/` bekommt fünf Kategorien: `manual/`, `operations/`, `findings/`, `security/`, `archive/`. Versionshistorie und ADRs bleiben separiert.
3. **Entsorgen ohne Verlust:** Superseded-Dokumente (historischer SECURITY_AUDIT, drei veraltete Reviews, ARENA_PROMPTS) wandern nach `docs/archive/` mit Historien-Banner – nichts wird gelöscht, nichts doppelt gepflegt.
4. **Fixen:** 1 kaputter Anchor, 4 Orphan-Links, Kommentar-Pfade in `setup_local.sh`, Docstring-Referenzen in 6 Tests, `views.py`-Pfadliste.
5. **Schützen:** Neuer `scripts/check_docs.py` (Link-/Anchor-/Orphan-Check) + GitHub-Actions-Job + Benennungskonvention + `patches/`-Workflow für Peer-Review-Vorschläge.

---

## 1. Audit – Befunde im Detail

### 1.1 Doppelte / redundante Markdown-Dateien

| Befund | Evidenz | Bewertung |
|---|---|---|
| **`README.md` (Root) vs. `docs/README.md`** | Jaccard-Wortüberlappung **0,41**; beide beschreiben Setup (`setup_local.sh`), Sicherheit, Version „2.4.19“; `docs/README.md` ist der alte, 2× längere Produkt-README (21 KB), Root-README der neue kurze (9,7 KB) | **Echt-Duplikat mit Drift-Risiko.** Beide enthalten install-/Compose-/Render-Anleitungen, die zusätzlich in `LOCAL_DEVELOPMENT.md` stehen → **dreifache** Pflege derselben Info |
| **`SECURITY_AUDIT.md` vs. `SECURITY_REVIEW_2.4.4.md`** | Der Audit (766 Z.) trägt selbst den Banner „Historischer Scan, nicht der aktuelle Freigabestatus“; der Review (18,9 KB) ist das aktuelle Statusdokument | Audit ist **superseded**, wird aber weiter als „wahr“ missverstanden, weil 15 Tickets + 6 Test-Dateien darauf verweisen |
| **`ARENA_AI_PROMPTS.md` (72 KB, 1734 Z.)** | Prompts 1–21; jeder einzelne trägt „Status: Fixed in 2.4.x“. Die bebilderten Inhalte (Befund + Fix) sind **wörtlich dupliziert** in den 15 `SEC/BUG/PERF/CODE`-Tickets und im CHANGELOG | Reines **Arbeitsdokument**, kein Pflegeobjekt. 72 KB redundanter Ballast auf dem aktuellen Stand |
| **`PEER_REVIEW.md` vs. `LOCAL_SETUP_PEER_REVIEW.md`** | Beide „Nachprüfung 2.4.4“-Reviews mit identischem Verweis auf `SECURITY_REVIEW_2.4.4.md`; `PEER_REVIEW.md` ist ein 149-Zeilen-Selbst-Review vom 2026-08-21 | Beide **historisch**; nur `LOCAL_SETUP_PEER_REVIEW.md` wird noch verlinkt (aus `docs/README.md`, das selbst aufgelöst wird) |
| **`Peer-Review_Backtesting-chatGPT.md` (1934 Z., 41 KB)** | Externes AI-Review *über* `BACKTESTING_STUDY.md`; wird von **nirgends** referenziert | Gehört ins Archiv, nicht in die aktive Doku |
| **Versionszahl „2.4.19“ hart codiert** in README, docs/README, MANUAL, … | 41× SECURITY_AUDIT, 36× CHANGELOG, 12× docs/README, 11× README (Muster `2.4.1x`) | `VERSION`-Datei ist bereits Single Source of Truth für Settings/UI/`/health/` (`trading_bot_project/settings.py:13`). **Doku-Duplikate der Zahl sind Wartungsschuld** → nur noch CHANGELOG enthält Versionszahlen (zweckbedingt) |

### 1.2 Verwaiste / ungenutzte Dateien (vom Entry-Point aus nicht erreichbar)

Referenzgraph-Analyse (quer über alle 29 MD-Dateien + Code):

| Datei | Referenziert von | Diagnose |
|---|---|---|
| `docs/PEER_REVIEW.md` | **niemand** | Orphan |
| `docs/Peer-Review_Backtesting-chatGPT.md` | **niemand** | Orphan |
| `docs/REMOTE_ACCESS_TAILSCALE.md` | **niemand** | Orphan (trotz aktuellem Inhalt!) |
| `docs/caddy-proxy-docker-config-manual.md` | **niemand** | Orphan (trotz aktuellem Inhalt!) |
| `docs/LOCAL_SETUP_PEER_REVIEW.md` | nur `docs/README.md` (wird aufgelöst) | wird mit Auflösung zum Orphan → Archiv |
| `docs/ARENA_AI_PROMPTS.md` | CHANGELOG, SEC-06 + 6 Test-**Docstrings** (nicht anklickbar) | de facto gepflegt, aber als toter Ballast |

> Zwei dieser Orphans (Tailscale, Caddy) sind **gute, aktuelle Anleitungen** – sie fehlen nur in jedem README/Index. Das ist ein Verlinkungs-, kein Inhaltsfehler.

### 1.3 Fehlerhafte / fragile Verlinkungen

1. **Kaputter Anchor (echt):** `docs/CHANGELOG.md` Zeile 30 → `PERF-21-info-api-db-aggregation.md#ci-und-auslieferung`. Zielheading existiert nicht; korrekt wäre `#auslieferung-und-pruefgrenzen`.
2. **193 relative Datei-Links: 0 kaputt.** ✅ (Erwartung war schlechter; sauberhalten durch Schutzskript, siehe §5)
3. **37 fragile CHANGELOG-Anker** (`CHANGELOG.md#241--2026-09-07`): funktionieren **nur** auf GitHub wegen des Double-Hyphen-Slug-Quirks (`[2.4.1] – 2026-…` → `241--2026-…`). Lokale Viewer (VS Code, mdbook, Pandoc) generieren andere Slugs → Sprung fehlt still. **Konvention:** nie auf Release-Anker verlinken, nur auf die Datei.
4. **Pfade in Kommentaren/Docstrings (verschwinden still bei Umzug):**
   - `scripts/setup_local.sh:157,170` → „siehe docs/FAQ.md Abschnitt 5“
   - `trading/tests/{test_bot_start_stop,test_cache_control,test_csrf_cookie,test_session_invalidate,test_csp,test_settings}.py` → „Basierend auf ARENA_AI_PROMPTS.md Prompt N und SECURITY_AUDIT.md Abschnitt X.Y“
   - `tests/test_compose_security.sh:10` → Kommentar „Findings-Dokumente in docs/“
5. **Harte Code-Pfadkopplung:** `trading/views.py:468-471` sucht das Manual unter `docs/MANUAL.md`, Fallback `MANUAL.md` (Root). `help_view`-Docstring (Z. 520) und `docs/MANUAL.md:307,387` erwähnen den Pfad ebenfalls. Tests `test_core.py::test_help_page_renders_manual` und `test_features.py` (Cache-Verhalten) hängen daran. **Jeder Manual-Umzug muss diese Liste mitziehen.**
6. **Selbst-erklärter Ticket-Mangel:** `SEC-06-rule-lifecycle-authz.md` beschreibt laut eigenem Text einen *nosniff*-Fix; der Dateiname `rule-lifecycle-authz` passt nicht zum Inhalt → Namensfehler beim Anlegen, im Plan korrigiert.
7. **Kein Blame/Review-Pfad:** `git log` = **1 Commit** („Add files via upload“). GitHub erkennt bei `git mv` in einem sauberen Commit Renames (`git mv` + `--follow`), bei 1-Commit-Historie gibt es nichts zu beschädigen – aber zukünftige Umzüge müssen **als reine Renames** committet werden, damit History erhalten bleibt.

### 1.4 Unlogische Struktur & Namenskonventionen

* **30 MDs flach in `docs/`** ohne Trennung nach Dokumenttyp (Manual ≠ Betrieb ≠ Findings ≠ Reviews ≠ Prompts ≠ Historie).
* **Drei Namensstile gemischt:** `SCREAMING_SNAKE` (SECURITY_AUDIT), `screaming-with-ticket-slug` (SEC-06-rule-lifecycle-authz), `lowercase-hyphen` (backtesting.md, caddy-proxy-docker-config-manual.md), dazu Deutsch/Englisch gemischt (`Peer-Review_Backtesting-chatGPT.md` – Bindestrich UND Unterstrich).
* **Root:** `config.template` ohne Endung (Env-Format; `.gitignore` führt `!config.template` als Negation – Endungsänderung erfordert `.gitignore`-Pflege!).
* `scripts/`, `tests/`, `trading/` sind **in Ordnung** und bleiben unverändert.

---

## 2. Zielstruktur

```
t-bot-lokal/
├── README.md                      ← EIZIGER Produkt-Einstieg (schlank; Setup-Ecke + Link-Karte)
├── CONTRIBUTING.md                ← NEU: Benennungs-/Dokupflege-/Patch-Workflow-Regeln
├── VERSION                        ← Single Source of Truth (unverändert)
│
├── patches/                       ← NEU: Peer-Review-Patch-Eingang (Inbox → accepted → done)
│   ├── README.md                  ← Ablauf: git am, Review-Checkliste, Statuspflege
│   ├── TEMPLATE-REVIEW.md         ← Review-Doppelkopf (Scope/Befund/Decision)
│   └── inbox/ accepted/ done/
│
├── scripts/
│   └── check_docs.py              ← NEU: Validierer (Links, Anker, Orphans, Duplikate, Konventionen)
│
└── docs/
    ├── README.md                  ← NUR noch Index: eine Liste pro Kategorie, kein Produkttext
    ├── CHANGELOG.md               ← bleibt (Anchor-Ziel; Release-Einträge = einzige Versions-Historie)
    ├── manual/                    ← Endanwender-Doku (was die App /help/ speist)
    │   ├── MANUAL.md              ← Pfad-Kopplung views.py! (oder bewusst in docs/ lassen, siehe §3 Phase 2B)
    │   └── BACKTESTING.md         ← aus backtesting.md (Konvention + Inhalt aktuell)
    ├── operations/                ← Betrieb & Infrastruktur
    │   ├── LOCAL_DEVELOPMENT.md
    │   ├── FAQ.md
    │   ├── REMOTE_ACCESS_TAILSCALE.md     ← Orphan → hier sichtbar verlinkt
    │   └── CADDY_PROXY.md                 ← Umbenennung caddy-proxy-docker-config-manual.md
    ├── findings/                  ← ein Dokument pro abgeschlossenem Audit-/Review-Befund (Tickets)
    │   ├── SEC-06-content-type-nosniff.md     ← Umbenennung (Name≠Inhalt gefixt)
    │   ├── SEC-07-session-lifetime-invalidation.md
    │   ├── SEC-08-cache-control-api.md
    │   ├── SEC-09-permissions-policy.md
    │   ├── SEC-10-information-disclosure.md
    │   ├── SEC-12-docker-default-passwords.md
    │   ├── BUG-12-race-condition-bot-start-stop.md
    │   ├── BUG-14-csv-echo-true-stream.md
    │   ├── PERF-17-db-trim-batch-delete.md
    │   ├── PERF-21-info-api-db-aggregation.md
    │   ├── CODE-18-indicator-dedup.md
    │   ├── CODE-19-view-type-hints.md
    │   ├── CODE-20-module-exports.md
    │   └── TEMPLATE.md            ← Pflichtvorlage für künftige Befunde (Status-Frontmatter)
    ├── security/                  ← aktuelle Freigabe-/Review-Dokumente (rollierend, nicht kumulativ)
    │   └── SECURITY_REVIEW_2.4.4.md
    ├── adr/                       ← Architektur-Entscheidungen (langfristige Referenz)
    │   └── ADR-0001-backtesting-worker-isolation.md   ← aus BACKTESTING_STUDY.md
    └── archive/                   ← superseded; NUR Lesezugriff, Banner-Pflicht, keine Pflege
        ├── SECURITY_AUDIT_2026-09-07.md
        ├── ARENA_AI_PROMPTS_2026-09-07.md
        ├── PEER_REVIEW_BUILD_2026-08-21.md            ← aus PEER_REVIEW.md
        ├── PEER_REVIEW_LOCAL_SETUP_2.3.0.md           ← aus LOCAL_SETUP_PEER_REVIEW.md
        └── PEER_REVIEW_BACKTESTING_CHATGPT_2026-08.md
```

**Warum diese Schnitte?**

* `manual` vs. `operations`: Nutzerdoku wird von der App gerendert (Code-Kopplung!), Betriebdoku nicht – unterschiedliche Änderungsanlässe.
* `findings/`: Die 15 Tickets sind zusammengehörige Arbeitsbelege des Audits; sie im Index nicht mehr einzeln zu listen entlastet README/CHANGELOG-Pflege. Ein Ticket = ein Befund, Frontmatter `Status:` ist die einzige Statuswahrheit (SEC-06..PERF-21 tragen ihn bereits als Text).
* `security/` bewusst dünn: **aktuelle** Reviews; abgeschlossene wandern nach `archive/`. Das verhindert den nächsten „SECURITY_AUDIT-vs-REVIEW“-Zustand.
* `archive/` statt Löschen: 6 Test-Dateien und 2 Tickets referenzieren SECURITY_AUDIT-Prompts/Paragraphen; Historie (Sperrvermerk „kein aktueller Freigabestatus“) bleibt auffindbar, aber nicht mehr im aktiven Kontext.
* `patches/` im Root (nicht `docs/patches`): Patches sind **Beitrittsartefakte, keine Doku** – `git am patches/inbox/*.patch` funktioniert so direkt, und der Ordner bleibt in `.gitignore`-Rules trivial greifbar.
* `backtesting.md` → `manual/BACKTESTING.md`: Die Doku-Konvention (Abschnitt 5.1) verlangt Großbuchstaben; Ausnahme bleibt `README`/`CHANGELOG` (Community-Konvention).

### 2.1 Vollständige Datei-Entscheidungstabelle (29 MDs + Root)

| Aktuell | Aktion | Neu | Betroffene Links/Code |
|---|---|---|---|
| `README.md` | **umschreiben**: bleibt Einstieg; Setup-Details → `operations/LOCAL_DEVELOPMENT.md`, Sicherheits-Absatz → 5 Bullets + Link auf `security/`; „Aktuelle Version 2.4.19“ durch **kein** hartes `x.y.z` ersetzen (Badge/Link auf `VERSION`-Datei) | `README.md` | Referenzen aus `docs/LOCAL_DEVELOPMENT.md`, `docs/README.md` |
| `docs/README.md` | **auflösen** → Unique Content (Render-Limits-Absatz, Passphrase-Absatz, QA-Befehle) nach `operations/LOCAL_DEVELOPMENT.md` übernehmen; Rest wird Index-Seite | `docs/README.md` (neu geschrieben) | Root-README verlinkt `docs/README.md` (Bleibt gültig) |
| `docs/MANUAL.md` | verschieben (oder in `docs/` belassen – §3 Phase 2B) | `docs/manual/MANUAL.md` | `views.py:469-470`, `views.py:520` (Docstring), `MANUAL.md` selbst:307/387, `CODE-18:10` |
| `docs/backtesting.md` | verschieben + umbenennen | `docs/manual/BACKTESTING.md` | `README.md:40`, `docs/README.md:7`, `CHANGELOG:372`, `CODE-18:10`, `MANUAL.md:307` (Text) |
| `docs/FAQ.md` | verschieben | `docs/operations/FAQ.md` | Root-README, CHANGELOG (2× Anker!), LOCAL_DEVELOPMENT, SEC-12; `scripts/setup_local.sh:157,170` (Kommentar) |
| `docs/LOCAL_DEVELOPMENT.md` | verschieben | `docs/operations/LOCAL_DEVELOPMENT.md` | Root-README, docs/README |
| `docs/REMOTE_ACCESS_TAILSCALE.md` | verschieben | `docs/operations/…` | keine (Orphan) → **neu verlinken** in Index + README-Betrieb |
| `docs/caddy-proxy-docker-config-manual.md` | verschieben + umbenennen | `docs/operations/CADDY_PROXY.md` | keine (Orphan) → **neu verlinken** |
| `docs/CHANGELOG.md` | bleibt | `docs/CHANGELOG.md` | 9 Dateien verlinken CHANGELOG (davon 37 fragiler Anker – siehe §3 Phase 3B) |
| `docs/SECURITY_REVIEW_2.4.4.md` | verschieben | `docs/security/SECURITY_REVIEW_2.4.4.md` | Root-README:41, LOCAL_DEVELOPMENT, docs/README, PEER_REVIEW, LOCAL_SETUP_PEER_REVIEW, SECURITY_AUDIT, ARENA_PROMPTS |
| `docs/BACKTESTING_STUDY.md` | umbauen zu ADR | `docs/adr/ADR-0001-…md` | docs/README, LOCAL_DEVELOPMENT |
| `docs/SEC-06-rule-lifecycle-authz.md` | verschieben + **umbenennen** | `docs/findings/SEC-06-content-type-nosniff.md` | Root-README, CHANGELOG, docs/README:175, SECURITY_AUDIT |
| `docs/{SEC-07..SEC-12,BUG-12,BUG-14,PERF-17,PERF-21,CODE-18..20}` | verschieben | `docs/findings/<gleich>` | je ~6 Links: Root-README, CHANGELOG, docs/README, SECURITY_AUDIT, ARENA_PROMPTS, MANUAL |
| `docs/SECURITY_AUDIT.md` | archivieren | `docs/archive/SECURITY_AUDIT_2026-09-07.md` | ARENA_PROMPTS, SEC-06; 6 Test-Docstrings (Text) |
| `docs/ARENA_AI_PROMPTS.md` | archivieren | `docs/archive/ARENA_AI_PROMPTS_2026-09-07.md` | CHANGELOG, SEC-06; 6 Test-Docstrings (Text) |
| `docs/PEER_REVIEW.md` | archivieren | `docs/archive/PEER_REVIEW_BUILD_2026-08-21.md` | keine |
| `docs/LOCAL_SETUP_PEER_REVIEW.md` | archivieren | `docs/archive/PEER_REVIEW_LOCAL_SETUP_2.3.0.md` | docs/README:7 |
| `docs/Peer-Review_Backtesting-chatGPT.md` | archivieren | `docs/archive/PEER_REVIEW_BACKTESTING_CHATGPT_2026-08.md` | keine |
| `config.template` | **optional Phase 5** | `config.template.env` | `.gitignore:!config.template`, `install.sh`-Erzeugerpfad, `tests/test_config_generation.sh` |

Regeln für jede Zeile „verschieben“: **`git mv` in einem eigenen Rename-Commit ohne inhaltliche Änderung** (GitHub-Erkennung), Linkpflege im **Folge-Commit** – so bleibt `git log --follow` und der Rename-Detector intakt.

---

## 3. Migrationsplan (Phasen)

### Phase 0 – Sicherungsnetz (0,5 h)

```bash
git switch -c arena/repo-restructure          # oder diese Session verwenden
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt django  # falls vorhanden
cd t-bot-lokal
.venv/bin/python manage.py test trading -q      # BASELINE (Grünstand notieren)
bash tests/run_tests.sh                          # BASELINE Shell-Tests
```

Validierungs-Gate: Baseline-Output in `tmp/baseline.txt` sichern (nicht committen – `tmp/` ignorieren oder `/tmp` nutzen). Bei rotem Baseline: Aufräum-PR **nicht** mit Testfixes mischen.

### Phase 1 – Konsolidierung ohne Bewegung (1 h)

1. **Duplikat-README:** Einzigartigen `docs/README.md`-Inhalt (Render-Free-Einschränkungen, Passphrase-Abschnitt, QA-Befehlsblock) nach `docs/LOCAL_DEVELOPMENT.md` migrieren (Abschnitte ergänzen, nicht anhängen – Struktur: Voraussetzungen → Setup → Betrieb → QA → Render).
2. Root-`README.md` auf ~40 Zeilen stutzen: Pitch, 3-Befehl-Setup, Sicherheits-Kurzliste, **Link-Karte** (Index unten), **keine Versionszahl mehr**.
3. **Kaputten Anchor fixen:** `docs/CHANGELOG.md:30` → `PERF-21-info-api-db-aggregation.md#auslieferung-und-pruefgrenzen`.
4. Orphans sichtbar machen: Tailscale + Caddy in README-Link-Karte und `docs/README.md` aufnehmen.
5. Commit: `docs: README-Konsolidierung, Ankerverweise, Orphans verlinken` → **PR #1** (rein inhaltlich, keine Pfade).

Gate: `python3 /tmp/checklinks.py`-äquivalenter Check (siehe §5 Skript) 0 Fehler; Diff gegen Baseline nur Dokumentation.

### Phase 2 – Struktur & Bewegung (1,5 h, mehrere Commits)

```bash
mkdir -p docs/{manual,operations,findings,security,adr,archive} patches/{inbox,accepted,done}

# 2A – risikoarme Bewegungen (nur Docs betroffen):
git mv docs/LOCAL_DEVELOPMENT.md docs/operations/
git mv docs/FAQ.md               docs/operations/
git mv docs/REMOTE_ACCESS_TAILSCALE.md docs/operations/
git mv docs/caddy-proxy-docker-config-manual.md docs/operations/CADDY_PROXY.md
git mv docs/SECURITY_REVIEW_2.4.4.md docs/security/
git mv docs/BACKTESTING_STUDY.md docs/adr/ADR-0001-backtesting-worker-isolation.md
for f in docs/SEC-0[6-9]*.md docs/SEC-1*.md docs/BUG-1*.md docs/PERF-1*.md docs/PERF-2*.md docs/CODE-1*.md docs/CODE-2*.md; do
  git mv "$f" "docs/findings/$(basename "$f")"
done
git mv docs/findings/SEC-06-rule-lifecycle-authz.md docs/findings/SEC-06-content-type-nosniff.md
# 2B – Manual (Code-Kopplung!):
git mv docs/MANUAL.md docs/manual/MANUAL.md
git mv docs/backtesting.md docs/manual/BACKTESTING.md

# 2C – Archiv-Bewegung:
git mv docs/SECURITY_AUDIT.md docs/archive/SECURITY_AUDIT_2026-09-07.md
git mv docs/ARENA_AI_PROMPTS.md docs/archive/ARENA_AI_PROMPTS_2026-09-07.md
git mv docs/PEER_REVIEW.md docs/archive/PEER_REVIEW_BUILD_2026-08-21.md
git mv docs/LOCAL_SETUP_PEER_REVIEW.md docs/archive/PEER_REVIEW_LOCAL_SETUP_2.3.0.md
git mv docs/Peer-Review_Backtesting-chatGPT.md docs/archive/PEER_REVIEW_BACKTESTING_CHATGPT_2026-08.md
```

**Alternative zu 2B (wenn Minimal-Risiko gewünscht):** `MANUAL.md` und `backtesting.md` in `docs/` belassen; dann entfallen die views.py-Änderungen. Empfehlung: **mitziehen** – die Kandidatenliste toleriert beide Pfade, der Fix ist 1 Zeile + Testlauf.

Regel pro Commit: nur `git mv` + `git commit -m "docs: <Cluster> verschoben (reine Renames)"` – **keine** Textänderungen im Rename-Commit.

### Phase 3 – Link-Reparatur maschinell (1 h)

**3A – relative Pfade anpassen.** Die Tickets verlinken untereinander und nach „oben“ aktuell als Same-Dir-Pfade (`SECURITY_AUDIT.md`, `CHANGELOG.md`, `MANUAL.md`, `backtesting.md`, `../VERSION`, `../trading/…`). Aus `docs/findings/` sind die korrekten Ziele `../archive/…`, `../CHANGELOG.md`, `../manual/…`, `../../…`. Einmalig mit diesem Skript (Vollversion in §5.1 als Dauerwächter):

```bash
python3 - << 'EOF'
import os, re
mv = {  # Basisname -> neuer Pfad relativ zu docs/<sub>/  bzw. docs/
 "SECURITY_AUDIT.md":"../archive/SECURITY_AUDIT_2026-09-07.md",
 "ARENA_AI_PROMPTS.md":"../archive/ARENA_AI_PROMPTS_2026-09-07.md",
 "PEER_REVIEW.md":"../archive/PEER_REVIEW_BUILD_2026-08-21.md",
 "LOCAL_SETUP_PEER_REVIEW.md":"../archive/PEER_REVIEW_LOCAL_SETUP_2.3.0.md",
 "SECURITY_REVIEW_2.4.4.md":"../security/SECURITY_REVIEW_2.4.4.md",
 "BACKTESTING_STUDY.md":"../adr/ADR-0001-backtesting-worker-isolation.md",
 "LOCAL_DEVELOPMENT.md":"../operations/LOCAL_DEVELOPMENT.md",
 "FAQ.md":"../operations/FAQ.md",
 "MANUAL.md":"../manual/MANUAL.md",
 "backtesting.md":"../manual/BACKTESTING.md",
 "CHANGELOG.md":"../CHANGELOG.md",
 "SEC-06-rule-lifecycle-authz.md":"SEC-06-content-type-nosniff.md",
}
for dp,_,fns in os.walk("docs"):
    for fn in fns:
        if not fn.endswith(".md"): continue
        p=os.path.join(dp,fn); s=open(p,encoding="utf-8").read(); o=s
        depth=len(p.split(os.sep))-2   # docs/x/f.md -> 2, docs/f.md -> 1
        for old,new in mv.items():
            n = new if dp!="docs" else new.replace("../","",1)
            # exakt denselben Linktext treffen (](alt), ](alt#anchor), ../alt), .. alt)
            s=re.sub(r"\]\("+re.escape("(../"*0)+os.path.relpath(old,dp)+r"([)#])", lambda m, n=n: "]("+n+m.group(1), s)
        if s!=o: open(p,"w",encoding="utf-8").write(s); print("fixed",p)
EOF
```

(Hinweis: Das Snippet ersetzt nur Same-Dir-Links; Root-`README.md` (`docs/…`-Präfix) und `../VERSION`→`../../VERSION` werden im selben Commit manuell nachgezogen – insgesamt laut Audit nur ~15 Stellen, alle durch §5-Check verifiziert.)

**3B – Anker-Konvention:** Alle `](CHANGELOG.md#<version>)`-Links auf `](CHANGELOG.md)` zurückführen (37 Stellen, fragil vs. lokale Viewer). Optional: CHANGELOG um stabile Ankermanker `<!-- {#v2419} -->` erweitern, falls Deep-Links dauerhaft gewünscht.

**3C – Code-/Kommentarreferenzen:**

```bash
# views.py Kandidatenliste (eine Zeile):
#   settings.BASE_DIR / "docs" / "manual" / "MANUAL.md"   (alt: docs/MANUAL.md)
sed -i 's|docs/MANUAL.md|docs/manual/MANUAL.md|g' trading/views.py   # Candidate + Docstring
sed -i 's|docs/FAQ.md Abschnitt 5|docs/operations/FAQ.md Abschnitt 5|' scripts/setup_local.sh
sed -i 's|ARENA_AI_PROMPTS.md|docs/archive/ARENA_AI_PROMPTS_2026-09-07.md|;s|SECURITY_AUDIT.md Abschnitt|docs/archive/SECURITY_AUDIT_2026-09-07.md Abschnitt|' trading/tests/test_bot_start_stop.py trading/tests/test_cache_control.py trading/tests/test_csrf_cookie.py trading/tests/test_session_invalidate.py trading/tests/test_csp.py trading/tests/test_settings.py
sed -i 's|docs/MANUAL.md|docs/manual/MANUAL.md|;s|docs/backtesting.md|docs/manual/BACKTESTING.md|' docs/manual/MANUAL.md
```

Danach **Volltext-Kontrolllauf:** `grep -rnE 'docs/(SECURITY_AUDIT|ARENA_AI_PROMPTS|PEER_REVIEW|LOCAL_SETUP_PEER_REVIEW|Peer-Review|caddy-proxy|REMOTE_ACCESS|LOCAL_DEVELOPMENT|FAQ|MANUAL|backtesting|SECURITY_REVIEW|BACKTESTING_STUDY|SEC-0|SEC-1|BUG-1|PERF-|CODE-)' --exclude-dir=.git .` → 0 Treffer außer beabsichtigten (`docs/operations/FAQ.md` etc.).

Commit: `docs: Pfad- und Ankerverweise nach Umstrukturierung aktualisiert`.

### Phase 4 – Archiv-Hygiene (0,5 h)

Jede Datei in `docs/archive/` erhält **oben** ein Standardbanner (eine `write`-Schleife genügt):

```markdown
> **Archiv – Stand <Datum>.** Dieses Dokument ist überholt; verbindlicher Status:
> [Security-Review 2.4.4](../security/SECURITY_REVIEW_2.4.4.md) bzw. [docs/README.md](../README.md).
> Inhaltlich nicht mehr in Pflege; Korrekturen werden nicht nachgezogen.
```

`docs/README.md` neu schreiben als **Index** (keine Produkttexte!): je Kategorie eine Tabelle „Datei · Status · Kurzbeschreibung“. Findings dort **nicht** einzeln auflisten, nur `findings/README.md` (auto-generierbar aus Frontmatter, siehe §5.2).

### Phase 5 – Schutz & Konventionen (2 h)

1. `scripts/check_docs.py` (§5.1) + `findings/TEMPLATE.md` (§5.2) + `patches/README.md`, `patches/TEMPLATE-REVIEW.md` (§4).
2. `CONTRIBUTING.md`: Namensregeln, Duplikat-Regeln, Review-/Patch-Workflow, Audit-Zyklus.
3. `.github/workflows/docs.yml` (§4.4) – **erster CI im Repo**: Link-/Orphan-/Konventions-Check + `manage.py check` + `trading`-Tests + `tests/run_tests.sh` + shellcheck + ruff.
4. `.gitignore` ergänzen: `tmp/`. `patches/inbox/` bleibt bewusst versioniert (Patches sind Diskussionsgrundlage für Reviews); abgearbeitete Patches wandern nach `accepted/`/`done/` und werden beim jährlichen Archiv-Aufräumen entfernt (siehe §4.3).
5. Optional (eigener Commit): `config.template` → `config.template.env` **inkl.** `.gitignore`-Negation und `install.sh`/`tests/test_config_generation.sh`-Pfadaktualisierung. Nur mitmachen, wenn Phase 1–4 grün sind.

---

## 4. Langfristige Wartbarkeit

### 4.1 Benennungskonvention (verbindlich, im `check_docs.py` geprüft)

* `docs/<kategorie>/<PRÄFIX>-<NN>-<kebab-slug>.md`; Präfix-Katalog: `SEC`, `BUG`, `PERF`, `CODE`, `ADR` (Nummern monoton steigend, nie wiederverwendet).
* **Zwei Stile, klar getrennt:** Findings/AD-Artefakte = Präfix+Schreibslug; Dauer-Dokumente (`MANUAL`, `FAQ`, `LOCAL_DEVELOPMENT`, `BACKTESTING`, `CADDY_PROXY`, `REMOTE_ACCESS_TAILSCALE`, `SECURITY_REVIEW_x.y.z`, `CHANGELOG`, `VERSION`) = `SCREAMING_SNAKE.md`. Ausnahme nur `README.md`.
* Kein Deutsch/Englisch-Mix im Slug (Englisch), keine Personennamen/Tools im Dateinamen (`-chatgpt` entfällt → Meta-Feld im Dokument), keine Leerzeichen, kein `manual-…-config-manual` (Doppelwörter vermeiden).
* Archivname = Originalname + `_YYYY-MM-DD`.
* Eine Datei, ein Thema, ein Home: Doku **verlinken statt kopieren**. Regeln im Einzelnen:
  * Setup/Install-Ablauf **nur** in `operations/LOCAL_DEVELOPMENT.md`; README zeigt 1 Befehl + Link.
  * Versionsnummern **nie** in Doku-Prosa (Quelle: `VERSION`; Historie: `CHANGELOG.md`).
  * Sicherheitsstatus **nur** in `docs/security/` (aktuell) und Finding-Frontmatter (Detail); Reviews verlinken Findings.

### 4.2 Duplikations-Prävention

1. **Findings-Frontmatter** (`docs/findings/TEMPLATE.md`) – `Status`-Feld ist die einzige Statuswahrheit; CHANGELOG-Abschnitte referenzieren nur das Ticket, wiederholen es nicht:

```markdown
---
id: SEC-13
title: Kurzbeschreibung
finding: MissingXYZ              # Scan-/Prompt-Kennung
severity: MEDIUM
status: open                     # open | in-review | fixed
fixed_in: null                   # z. B. 2.4.20
audit_ref: docs/archive/SECURITY_AUDIT_2026-09-07.md#2-13
tests: trading/tests/test_xyz.py
---
## Befund · ## Fix und Umfang · ## Regressionstests · ## Prüfgrenzen
```

2. **Archive-Regel:** Wird ein Review durch einen neuerlichen ersetzt, wandert das alte Dokument **im selben PR** nach `docs/archive/` + Banner. Damit gibt es genau ein „aktuell“-Dokument pro Gattung. (Genau dieser Regelverfall erzeugte den aktuellen SECURITY_AUDIT-Zustand.)
3. **KI-/Agentur-Arbeitsdokumente** (Prompts, Chat-Exporte) entstehen in `patches/inbox/` oder `docs/archive/`, **nie** in `docs/`-Aktivbereich.
4. CHANGELOG-Eintrag ist Pflichtfeld jedes PRs (`keepachangelog`-Format, das er bereits nutzt); Duplikat-Erkennung über §5.1-Check „Identischer Absatz in ≥2 Dateien“ (Warnung).

### 4.3 Peer-Review-Patch-Workflow (`patches/`)

```
patches/
  README.md        # Prozess (unten)
  TEMPLATE-REVIEW.md
  inbox/0001-peerview-rate-limit-tuning.patch
  accepted/        # nach Merge ins Haupt-Line: git-am-quittierte Serie hierhin
  done/            # abgelehnt/erledigt (optional)
```

Ablauf (in `patches/README.md` festgehalten):

1. Beitragender legt `git format-patch`-Serie + `REVIEW`-Kommentarblock (Scope, Befunde, Teststatus) als `patches/inbox/<name>.patch|.md` ab (PR-Alternative: PR-Review-Kommentare bleiben erstattungsfähig).
2. Maintainer prüft: `git am patches/inbox/0001-*.patch` auf Review-Branch, `bash tests/run_tests.sh` + `.venv/bin/python manage.py test trading -q`.
3. Annahme → PR in den Hauptzweig; Patch wandert nach `accepted/`, Finding-Status (`status: fixed`) + CHANGELOG-Eintrag werden gesetzt.
4. Ablehnung → Antwort im PR/Review-Datei mit Begründung; Datei nach `done/`.
5. **Nie** direkt in `docs/manual`/`docs/security` patchen – Änderungen laufen über Findings/PR, sonst entsteht Duplikatsdruck.

### 4.4 Regelmäßige Audits & CI

* **Jeder PR:** `.github/workflows/docs.yml`:
  - `python scripts/check_docs.py --all` (0 Broken-Links, 0 Orphans, 0 Konventionsverstöße – harte Gates)
  - `python manage.py check`, `manage.py test trading -q`
  - `bash tests/run_tests.sh`; `shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh`
  - `ruff check .`, `mypy trading/views.py` (Konfiguration existiert bereits in `pyproject.toml`)
* **Quartalsweise Doku-Audits** (Kalendereintrag, Checklist im Repo unter `docs/README.md` verlinkt):
  1. `check_docs.py --orphans --dupes` durchsuchen; Funde abarbeiten (findings-Ticket für jedes „Fix“-Bedarf)
  2. Jeder `Status: open`-Eintrag: altert? → im nächsten Release schließen oder in `open` dokumentieren
  3. Ein Security-Scan-Turnus (Bandit + `pip-audit -r requirements.txt`, wie in SECURITY_REVIEW dokumentiert) → neues Review-Dokument, altes archivieren
* **Jährlich:** Archiv-Aufräumen (Archive-Dateien können in GitHub-Release-Assets ausgelagert werden), `VERSION`- und Changelog-Hygiene, Dep-Inclusivity.

---

## 5. Validierung (pro Phase) & Werkzeug

### 5.0 Validierungsmatrix

| Prüfschritt | Befehl | Erwartung nach Phase… |
|---|---|---|
| Markdown-Linkcheck (Dateien+Anker) | `python scripts/check_docs.py` | 0 broken / 0 orphans ab **1** (Orphans), ab **3** (alle Pfade) |
| App-Hilfe rendert | `python manage.py test trading.tests.test_core -v 1` (help-Test) + Browser `/help/` | ab **2B/3C** kritisch (views.py-Kopplung) |
| Manual-Cache-Test | `python manage.py test trading.tests.test_features -v 1` | ab **2B/3C** |
| Setup-Skripte | `bash tests/run_tests.sh` | unverändert grün (Phase 0 Baseline) |
| Compose-Interpolation | `docker compose config -q` | Phase 5 (falls `config.template`-Rename) |
| Test-Docstrings aktuell | `grep -rn 'ARENA_AI_PROMPTS\|SECURITY_AUDIT' trading/tests/ | grep -v archive` | 0 ab **3C** |
| Volltext-Altref-Scan | `grep -rnE 'docs/(SECURITY_AUDIT|ARENA|PEER|caddy|backtesting|MANUAL)' --exclude-dir=.git .` | 0 ab **3C** |
| Git-Rename-Erkennung | `git show --stat <Migrations-Commits>` | Zeigt `R100` für reine Bewegungen (nach **2**) |
| Links im GitHub-UI | PR-Vorschau | Ankersprünge funktionsfähig nach **3** |

### 5.1 `scripts/check_docs.py` (Dauerwächter – bei Implementierung genau so übernehmen)

```python
#!/usr/bin/env python3
"""Doku-Waechter: Links, Anker, Orphans, Konventionen. Exit!=0 bei Verstoess."""
import os, re, sys
ROOT = os.getcwd()
LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
def gh_slug(t):  # GitHub-kompatibler Heading-Slug
    t = re.sub(r'[*`\[\]()]', '', t.strip().lower())
    t = re.sub(r'[^\w\s-]', '', t)
    return t.replace(' ', '-')
def headings(p):
    if not os.path.exists(p): return None
    out = set()
    for line in open(p, encoding="utf-8", errors="replace"):
        m = re.match(r'#{1,6}\s+(.*)', line)
        if m: out.add(gh_slug(m.group(1)))
    return out
mds = [os.path.join(d,f) for d,_,fs in os.walk(ROOT) if '.git' not in d
       for f in fs if f.endswith('.md')]
def strip_fenced(text):  # Zeilen in ```-Bloegen zaehlen nicht als Links
    out, fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"): fence = not fence; out.append(""); continue
        out.append("" if fence else line)
    return "\n".join(out)
problems, referenced = [], {os.path.relpath(m, ROOT): 0 for m in mds}
for fp in mds:
    text = strip_fenced(open(fp, encoding="utf-8", errors="replace").read())
    for m in LINK.finditer(text):
        link = m.group(2)
        if link.startswith(('http://','https://','mailto:')): continue
        target, _, anchor = link.partition('#')
        rel = os.path.normpath(os.path.join(os.path.dirname(fp), target)) if target else fp
        if target and not os.path.exists(rel):
            problems.append(f"{fp}: fehlt -> {link}"); continue
        if target: referenced.setdefault(os.path.relpath(rel, ROOT), 0); referenced[os.path.relpath(rel, ROOT)] += 1
        if anchor and headings(rel) and gh_slug(anchor) not in headings(rel):
            problems.append(f"{fp}: Anker nicht gefunden -> {link}")
    # Version-Zahl in Doku-Prosa (außer CHANGELOG/VERSION/ARCHIV) verbieten:
    if not re.search(r'(docs/(archive/)?CHANGELOG|VERSION|docs/archive/)', fp):
        for mm in re.finditer(r'(?<![\d.])\d+\.\d+\.\d+(?![\d.])', text):
            problems.append(f"{fp}: Version-Zaehl {mm.group(0)} hartcodiert")
for p, n in referenced.items():
    if n == 0 and not p.endswith(('README.md','CHANGELOG.md')):
        problems.append(f"ORPHAN (von keinem MD referenziert): {p}")
for p in problems: print(p)
sys.exit(1 if problems else 0)
```

### 5.2 `docs/findings/TEMPLATE.md`

Der Frontmatter-Block aus §4.2.1 + vier Pflichtabschnitte; README im findings-Ordner listet alle Tickets automatisch über die `Status`-Felder (ein 20-Zeilen-Skript, das beim Audit ausgeführt die Tabelle generiert – in `check_docs.py` integrierbar).

---

## 6. Aufwand & Reihenfolge (Realitätscheck)

| Phase | Aufwand | Risiko | Rollback |
|---|---|---|---|
| 0 Baseline | 0,5 h | – | – |
| 1 Konsolidierung | 1 h | niedrig (nur Text) | `git revert` |
| 2 Bewegung | 1,5 h | **Mittel** – Manual/views | Renames einzeln revertierbar |
| 3 Links | 1 h | niedrig, skriptgestützt + §5-Check | `git revert` |
| 4 Archiv + Index | 0,5 h | niedrig | `git revert` |
| 5 Schutz/CI | 2 h | niedrig (neu, nichts Altes) | `git revert` |

**Gesamt ≈ 6–7 h**, aufteilbar in 5–6 PRs. Keine Migration erfordert Daten-/DB-Aktionen; `manage.py`, Migrationsordner, Compose und Deployment-Dateien (`Dockerfile`, `render*.yaml`) bleiben unangetastet – das gilt es bei Phase 5 (`config.template`-Rename, einziger potenzieller Deployment-Eingriff) zu respektieren: separat reviewen oder weglassen.
