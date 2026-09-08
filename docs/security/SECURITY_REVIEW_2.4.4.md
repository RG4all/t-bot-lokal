# Security-Review und Release-Nachweis 2.4.4

**Datum:** 2026-09-07 · **Ausgangsstand:** `64b4949` / Version 2.4.3

**Release:** Patch-Version 2.4.4 · **Commit-Titel:** `fix(security): replace hardcoded passphrase with dynamic generation`

## Umfang und Aussagegrenzen

- Repositoryweiter statischer Scan der eigenen Python-Module in `trading/`, `trading_bot_project/` und `scripts/` mit Bandit; Tests und historische Migrationen aus dem Sicherheitsmuster-Scan ausgeschlossen. Abschließender Umfang: 7.370 Codezeilen.
- Vertieft geprüft: Secret-Auflösung in Settings, Passphrase-View, HTTP-Gate, Signed-Cookie-Nachweise, WebSocket-Verbindungsautorisierung, Auth-Rate-Limit, Docker-/Render-Defaults, lokale Env-Generatoren und die betroffenen Tests.
- Die vom Scan markierten Markdown-/SVG-Ausgaben, Subprozessaufrufe und Zufallsquellen wurden am konkreten Datenfluss geprüft. Eigene Templates wurden auf Inline-Skripte/Event-Handler geprüft; Ruff und ShellCheck wurden auf die eigene Python- bzw. Shell-Codebasis angewendet.
- Direkte und aufgelöste transitive Python-Abhängigkeiten wurden mit `pip-audit -r requirements.txt` geprüft. Keine neue Runtime-Abhängigkeit eingeführt.
- **Kein vollständiger Penetrationstest und keine pauschale Produktionsfreigabe:** Die Handels-/Backtesting-Algorithmen, jede Exchange-Fehlerantwort, alle historischen Audit-Vorschläge und vendorte JavaScript-Bibliotheken wurden nicht vollständig Zeile für Zeile neu auditiert. Reale Proxy-Topologie, produktive Rollen/Secrets, Render-Runtime und Container-/OS-CVEs sind hier nicht verifiziert. Ein unabhängiger menschlicher Peer-Review bleibt Teil des PR-Prozesses.

## Bestätigte Sicherheitsbefunde – nach Priorität

### S1 – Hoch: Öffentliche Passphrase und öffentlicher Cookie-Signierschlüssel

- **Kategorie:** Authentifizierung, Datenexposition, kryptographisches Key-Management.
- **Risiko: echt.** `settings.py` nutzte ohne `PASSPHRASE` einen bekannten Repository-Wert. Außerhalb von Render war auch bei `DEBUG=False` ein öffentlicher `SECRET_KEY` als Fallback möglich. Da `SESSION_ENGINE` auf `signed_cookies` steht, konnte jemand mit diesem Schlüssel eine Gate-Freigabe signieren, selbst wenn nur die Passphrase zufällig geworden wäre. Compose und Installer hatten weitere bekannte App-Defaults.
- **Erreichbarkeit/Schaden:** Bei einer erreichbaren Instanz mit diesen Defaults konnte ein nicht angemeldeter Angreifer die vorgeschaltete Zugangskontrolle passieren. Das ist **nicht automatisch Zugriff auf fremde Konfigurationen**: Benutzer-Login und objektbezogene Eigentümerprüfung sind weitere Kontrollen.
- **Remediation umgesetzt:** Lokale Settings verwenden `secrets.token_urlsafe(32)` für die Passphrase und `secrets.token_urlsafe(50)` für einen fehlenden Signierschlüssel. Render und `DEBUG=False` verlangen explizite, nichtleere Secrets. Compose verlangt beide App-Secrets; Installer/Tuner generieren private Passphrasen. Es gibt keine öffentliche App-Default-Passphrase mehr in den aktiven Startpfaden.
- **Nachweis:** `PassphraseSettingsTests`, `PassphraseGateTests`, Hardware-Tuner-Tests sowie `tests/test_config_generation.sh` und `tests/test_setup_local.sh`. Reale Zufallswerte, unveränderte explizite Werte, fehlende/leere/Leerraumwerte, Entropiefehler und alle Produktions-Startbedingungen werden geprüft.
- **Betriebsmaßnahme:** Bereits eingesetzte öffentliche Defaults ersetzen. Vorhandene explizite Secrets werden absichtlich nicht stillschweigend überschrieben; die Anwendung kann deren tatsächliche Geheimhaltung/Entropie nicht beweisen.

### S2 – Hoch: Docker-Image deaktivierte den Gate auch auf Render

- **Kategorie:** Authentifizierungs-/Konfigurationsfehler.
- **Risiko: echt.** `Dockerfile` setzte `PASSPHRASE_GATE_ENABLED=False`. `render.yaml` verwendete genau dieses Image, überschrieb den Schalter jedoch nicht. Die Middleware übersprang dann den Gate unabhängig von der konfigurierten Passphrase.
- **Erreichbarkeit/Schaden:** Öffentliches Render-Deployment ohne die beabsichtigte Vorab-Zugangskontrolle; reguläre Benutzerautorisierung bleibt davon getrennt.
- **Remediation umgesetzt:** Gate-Opt-out aus dem Basis-Image entfernt, Render setzt ihn explizit auf `True`. In Settings führen deaktivierte Gate-Werte auf Render oder bei `DEBUG=False` zum `RuntimeError`. Nur das lokale DEBUG-Compose-Profil darf optieren. Das Setup bewahrt einen bereits eingeschalteten Gate beim Retuning.
- **Nachweis:** Regressionstest liest die tatsächlichen `ENV`-Vorgaben des Dockerfiles und kombiniert sie mit Render-Settings; Startmatrix prüft auch `RENDER=True, DEBUG=True` und ungültige Gate-Schalter. Shell-Test prüft Retuning mit aktivem Gate.

### S3 – Mittel: Manipulierbare und nicht atomare Auth-Rate-Limits

- **Kategorie:** Authentifizierung/Brute-Force-Schutz.
- **Risiko: echt.** `RateLimitMiddleware` vertraute bedingungslos dem ersten `X-Forwarded-For`-Wert. Ein direkter Client konnte mit wechselnden Headern beliebig neue Zähler anlegen. Prüfung und Reservierung verwendeten getrennte Lock-Abschnitte; parallele POSTs konnten das Limit überschreiten. Inaktive IP-Einträge blieben unbegrenzt im Speicher.
- **Erreichbarkeit/Schaden:** Auf erreichbaren Auth-Endpunkten mehr Passwortversuche als zugesichert; zusätzlich Speicherwachstum. Hinter einem zuverlässig bereinigenden Proxy ist der Header-Angriff eingeschränkt, aber diese Vertrauensgrenze war nicht konfiguriert.
- **Remediation umgesetzt:** Standardmäßig ausschließlich `REMOTE_ADDR`; optionale `RATE_LIMIT_TRUSTED_PROXIES` als IP-/CIDR-Allowlist. Proxy-Kette wird von rechts bis zum ersten nicht vertrauten Hop geprüft; ungültige Header eröffnen keinen neuen Zähler. Atomare Reservierung, monotone Zeit, Ablauf auch inaktiver Einträge und maximal 10.000 IP-Zähler; bei voller Map werden neue IPs vorübergehend gesperrt statt aktive Limits zu verdrängen. Admin-Login ist ebenfalls erfasst.
- **Nachweis:** 16 parallele POSTs ergeben genau fünf akzeptierte Requests; Tests für Header-Spoofing, IPv4/IPv6-Proxys, ungültige Konfiguration, Script-Präfix, Ablauf und Speichergrenze.
- **Betriebsmaßnahme:** Proxy-Peer-Netze erst nach Prüfung der realen Topologie eintragen. Ohne Allowlist teilen Proxy-Nutzer dessen IP-Limit. Das Limit bleibt **prozesslokal**; bei mehreren Web-Prozessen/Instanzen zusätzlich ein gemeinsames Limit am vertrauenswürdigen Edge/Proxy oder in einem geteilten Backend einsetzen.

### S4 – Mittel: Gate-Freigaben überlebten eine Passphrase-Rotation

- **Kategorie:** Session-Handling/Autorisierung.
- **Risiko: echt.** Die Session enthielt nur `passphrase_verified=True`. Eine unter einem bekannten alten Wert erhaltene Freigabe blieb nach dem Setzen einer neuen Passphrase bis zum Cookie-Ablauf gültig. Der WebSocket-Consumer prüfte nur angemeldeten Eigentümer und Task, nicht den Gate-Zustand.
- **Erreichbarkeit/Schaden:** Besitzer alter gültiger Cookies konnten die neue Gate-Passphrase umgehen. Bei WebSockets war zusätzlich ein authentifizierter Task-Eigentümer nötig; ein fremder Task wurde dadurch nicht zugänglich.
- **Remediation umgesetzt:** Gemeinsames Modul `trading/passphrase.py` bildet einen HMAC-SHA256-Nachweis unter dem privaten Django-Signierschlüssel. HTTP-Middleware und Gate-View prüfen den aktuellen Nachweis; alte boolesche Werte werden verworfen. WebSockets prüfen Gate und Eigentümerschaft beim Verbindungsaufbau. Kein Klartext-Secret im Cookie.
- **Nachweis:** Tests für alte boolesche/ungültige Nachweise, Passphrase-/Signierschlüssel-Rotation und WebSocket-Eigentümerprüfung mit/ohne Gate.
- **Abgrenzung:** Das ersetzt nicht die gesamte Django-Session-Architektur. Signed-Cookie-Sessions besitzen weiterhin keinen zentralen serverseitigen Einzel-Session-Widerruf. Für zwingenden Widerruf gestohlener Login-Cookies nach Logout muss mit geklärter DB-/Redis-Verfügbarkeit ein serverseitiges Session-Backend vorgesehen werden; die bisherige DB-Ausfalltoleranz ist dabei eine Architekturentscheidung. Eine Rotation des privaten `SECRET_KEY` invalidiert alle bestehenden Cookies.

### S5 – Niedrig: Unicode-Eingaben lösten einen Gate-Fehler aus

- **Kategorie:** Authentifizierung/Verfügbarkeit.
- **Risiko: echt, aber begrenzt.** `secrets.compare_digest()` mit zwei Python-Strings unterstützt nur ASCII. Nicht-ASCII-POSTs erzeugten einen `TypeError`; eine explizite Unicode-Passphrase war nicht zuverlässig verwendbar. Das einseitige `.strip()` konnte außerdem konfigurierte Passphrasen mit Rand-Leerzeichen unbenutzbar machen.
- **Remediation umgesetzt:** Djangos bytebasierter `constant_time_compare()` ohne stilles Trimmen. Nichtleere explizite Environment-Werte bleiben exakt erhalten. Passphrase-POST und Vergleichsvariable sind für Django-Fehlerberichte als sensibel markiert.
- **Nachweis:** Falsche Unicode-Eingaben werden regulär abgelehnt; korrekte Unicode-Passphrasen mit Leerzeichen funktionieren. CSRF-Schutz und Fehlerbericht-Redaktion sind getestet.

## Verhindertes Regressionrisiko im vorgeschlagenen Fix

**Mittel – Passphrase in Produktionslogs:** Der ursprüngliche Lösungsvorschlag hätte bei jedem Nicht-Render-Start, also auch bei `DEBUG=False`, den erzeugten Wert im Klartext geloggt. Das wäre bei zentral gesammelten Produktionslogs eine zusätzliche Secret-Exposition gewesen. Dies war **kein vorhandener Log-Leak im Ausgangscode**, sondern ein Risiko bei unverändertem Übernehmen des Vorschlags.

Die Implementierung begrenzt Generierung **und** Passphrase-WARNING auf lokalen DEBUG-Betrieb ohne Render. Produktionsstarts brechen vorher ab; explizite Passphrasen und generierte Signierschlüssel werden nicht ausgegeben. `DEBUG=True` ohne Render ist keine automatische Sicherheitserkennung und darf nicht als Produktionskonfiguration verwendet werden. Lokale Logs mit temporären Passphrasen bleiben bewusst sensibel. Die entsprechenden Positiv-/Negativfälle sind in den Settings-Tests abgedeckt.

## Mitbehobene funktionale Probleme

| Priorität | Befund und Kontext | Fix / Nachweis |
|---|---|---|
| Hoch (Verfügbarkeit, kein eigenständiger Auth-Exploit) | Modell enthielt bereits `leverage` und `trade_direction`, Migration fehlte. Der Ausgangstestlauf hatte 27 DB-Fehler und einen veralteten Formular-Fixture-Fehler; frische Installationen konnten Konfigurationen nicht normal verwenden. | Additive Migration `0014_configuration_leverage_trade_direction`; Formular-Fixture ergänzt. Frische Migrationen sowie `0013 → 0014 → 0013` mit vorhandener Konfiguration geprüft: Name bleibt erhalten, Defaults sind `1`/`long`. Keine Änderung des Trading-Algorithmus. |
| Mittel (Funktion) | `script-src 'self'` blockierte eigene Inline-Template-Skripte und Event-Handler; die deutsch lokalisierte Dezimalzahl in der Backtest-Laufzeitschätzung war ungültige JS-Syntax. | Request-Nonces nur für eigene Template-Skripte, Event-Listener statt Inline-Handler, `unlocalize` für die JS-Zahl. Header-/Body-Nonce-Konsistenz, neue Nonce je Request, Template-Scan und deutsche Darstellung getestet. Kein pauschales `unsafe-inline` für Skripte. |
| Niedrig (Wartbarkeit/Dateischutz) | Veraltete Render-Arbeitsbranch-Verweise, unbenutzte Test-Imports, doppelte Ignore-Regeln; Secrets-Dateien teils erst nach dem Schreiben auf Modus 0600 gesetzt. | Render-Vorlagen auf Integrationsbranch `tbot.local`; gezielte Bereinigung. Private Erstellung bzw. Einschränkung vorhandener Secrets-Dateien vor neuen Secret-Bytes, Modus-/Idempotenztests. |

## Prüfung der fünf Sicherheitskategorien und False Positives

| Kategorie | Konkrete Scan-Stelle | Bewertung |
|---|---|---|
| Injection/XSS | Bandit B703/B308 bei `_render_manual()`/`mark_safe()` in `trading/views.py` | Im geprüften Datenfluss **False Positive**: Markdown stammt ausschließlich aus der versionierten lokalen Handbuchdatei, nicht aus HTTP-Eingaben. Das ist keine Erlaubnis, später Nutzer-Markdown ungeprüft einzusetzen. |
| Injection/XSS | Bandit B703/B308 bei `_equity_svg()` | **False Positive** im aktuellen Generator: Symbol und Achsenbeschriftungen werden HTML-escaped, Koordinaten aus numerisch konvertierten, endlichen Werten erzeugt. Ein vorhandener Regressionstest prüft bösartige Labels und nichtendliche Werte. |
| Command Injection | Bandit B404/B603/B607 in `scripts/local_hardware.py` | Kein belegter Remote-Injection-Pfad: fester Argumentvektor `["sysctl", "-n", "hw.memsize"]`, kein `shell=True`, keine HTTP-/Nutzereingaben. Der lokal ausführende Betreiber muss einen vertrauenswürdigen `PATH` verwenden; dessen Kontrolle ist eine bestehende lokale Vertrauensgrenze. |
| SQL/NoSQL/LDAP Injection | Eigene Python-Quelltextsuche; Gate nutzt keinen dynamischen SQL-Text, Objektzugriffe erfolgen über ORM | Im geprüften Gate-/Autorisierungsumfang kein bestätigter Fund. Das ist kein vollständiger Nachweis aller Datenflüsse der Gesamtanwendung. |
| Authentifizierung/Autorisierung | Settings, Gate, Consumer, Rate-Limit | Bestätigte Befunde S1–S4; zusätzliche Benutzer-/Eigentümerprüfungen bleiben erhalten. |
| Datenexposition | Öffentliche App-Defaults; lokale Warning-Ausgabe; B105/B106 für `""` im historischen `clear_api_keys`-Kommando | Defaults behoben, Warning lokal begrenzt. Die Bandit-Leerstring-Funde sind **keine eingebauten Zugangsdaten**, sondern Leerwert-Vergleich/Zuweisung. Das ist keine Bestätigung der Funktionsfähigkeit des historischen Bereinigungskommandos auf beliebigen Altschemata oder Backups. |
| Unsichere Abhängigkeiten | Exakt gepinnte `requirements.txt` einschließlich 84 aufgelöster Pakete | `pip-audit`: **keine bekannten Schwachstellen** zum Prüfzeitpunkt. Kein CVE-Fund wird allein aus dem Alter einer Version abgeleitet. Container-/OS-Pakete und vendorte JS-Dateien sind hier nicht CVE-auditiert; die transitiven Versionen sind nicht als vollständiges Lockfile eingecheckt. |
| Kryptographie | Bandit B311 in DB-/Marktdaten-Retry-Pfaden | **False Positive** für kryptographische Unsicherheit: `random.uniform()` steuert nur Backoff-Jitter, keine Secrets oder Zugriffstoken. Secrets verwenden Betriebssystementropie über `secrets` bzw. `/dev/urandom`; Gate-Nachweise HMAC-SHA256. |

Bandit meldet abschließend **12 Rohbefunde** (vier Medium-/acht Low-Meldungen, teilweise Doppelmeldungen für dieselbe Stelle), die oben kontextbezogen eingeordnet sind. Der Scan wird nicht durch globale `nosec`-Ausnahmen künstlich grün gemacht. Die frühere B104-Meldung zu `"0.0.0.0"` im IP-Fallback war keine Socket-Bindung; der neue Fallback lautet `unknown`.

## Testprotokoll und Reproduktion

Ausführung in einer isolierten `.venv` mit den unveränderten Runtime-Pins; Python **3.11.2**, Django **5.2.17**, Ruff **0.16.6**, ShellCheck **0.11.0**, Bandit **1.9.4**, pip-audit **2.10.1**.

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
bash tests/run_tests.sh
ruff check .
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
python -m pip check
pip-audit -r requirements.txt
bandit -r trading trading_bot_project scripts -x trading/tests,trading/migrations
```

Für Deploy-Checks private **Testwerte** nur in die aktuelle Shell exportieren, dann:

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export PASSPHRASE="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
DEBUG=False RENDER=True python manage.py check --deploy --fail-level WARNING
DEBUG=False RENDER=True python manage.py migrate --noinput
DEBUG=False RENDER=True python manage.py collectstatic --noinput
```

- Django-Systemcheck und Produktions-Deploy-Check: keine Probleme/Warnungen.
- `makemigrations --check --dry-run`: keine fehlenden Migrationen nach Ergänzung von 0014.
- **133 Django-/Python-Tests** und **7/7 Shell-Testgruppen** bestanden.
- Branch-Coverage-Lauf: **100 % für `trading/passphrase.py` und `trading/rate_limit.py`**. Die gesamten Settings haben 87 %; nicht alle DB-/Redis-Konfigurationspfade waren Teil dieser Matrix.
- Migrationen auf frischer SQLite-DB und Upgrade/Reverse mit vorhandenen Daten erfolgreich; keine produktiven Daten verwendet.
- `collectstatic` erfolgreich: 135 Dateien kopiert / 675 Nachverarbeitungen.
- Ruff/ShellCheck fehlerfrei; `pip check` ohne Konflikte; Dependency-Audit ohne bekannten CVE-Fund. Bandit liefert die oben erklärten Rohbefunde und deshalb absichtlich keinen pauschal grünen Exit-Code.

### Nicht ausführbar in dieser Sandbox

- Kein Docker installiert: kein Image-Build, keine Compose-/Distro-Smoke-Tests, kein PostgreSQL-/Redis-/Render-Livetest.
- Das Docker-Zielruntime Python 3.12.7 konnte wegen TLS-/Downloadbeschränkungen nicht zusätzlich installiert werden; ausgeführt wurde Python 3.11.2. Das darf nicht als verifizierter 3.12-Container-Test dargestellt werden.
- Nativer PDF-Smoke-Test ist wegen fehlender Pango-Bibliotheken blockiert. Nachinstallation aus Debian-Paketquellen war ebenfalls durch Netzwerk/TLS blockiert. Der Dockerfile installiert diese Bibliotheken bereits; echtes PDF-Rendering ist deshalb im Zielimage vor Freigabe nachzuholen.
- Kein Browser-End-to-End-Test: CSP wurde über ausgelieferte Header, Template-Inhalt und Regressionen geprüft, nicht über eine reale Browser-/Proxy-Kette.

## Rollout und verbleibender Kontext

1. Private App-Secrets für Web, Worker und Scheduler konsistent bereitstellen; `DEBUG=False`, Gate aktiv. Öffentlich bekannte alte Werte ersetzen. Nicht versuchen, die Startprüfung durch DEBUG in Produktion zu umgehen.
2. Version 2.4.4 deployen und Migration 0014 anwenden. Keine Volumes für App-Secret-Rotation löschen.
3. Nach Upgrade/Rotation den Gate erneut passieren. Signierschlüssel-Rotation invalidiert zusätzlich Django-Login-Cookies; ein zentraler Einzel-Session-Widerruf ist nicht Teil dieses Patches.
4. Tatsächliche Proxy-Peer-IPs/CIDRs und Header-Bereinigung verifizieren. Ohne diese Information lässt sich weder sicheres Vertrauen in Forwarded-Header noch ein korrektes pro-Nutzer-IP-Limit hinter dem Proxy zusichern. Für mehrere Web-Prozesse gemeinsames Limit vorsehen.
5. Im Zielimage Python-/PDF-/Container-Tests sowie in der Zielumgebung Gate, Login, CSRF, Eigentümerprüfungen und WebSocket-Fortschritt ausführen. Bis dahin bleibt die vollständige Deployment-Freigabe offen, auch wenn der Code-Fix und die hier ausführbaren Regressionen erfolgreich sind.

**Prioritätsreihenfolge:** zuerst S1/S2 (öffentliche Secrets und deaktivierter Produktions-Gate), dann S3/S4 (Rate-Limit und Rotation), dann S5 (Unicode/Verfügbarkeit). Das Log-Regressionsrisiko und die funktionalen Testblocker sind mitbehoben; die oben benannten Runtime-/Deployment-Prüfungen bleiben vor einer produktiven Freigabe erforderlich.
