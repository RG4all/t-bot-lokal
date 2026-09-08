# Fernzugriff t-bot via Tailscale (DS-Lite / Vodafone, ohne FritzBox, 0 €)

> Stand: N150 (CachyOS, x86_64), t-bot Docker-Stack `web:8369`, Vodafone DS-Lite
> (geteilte IPv4 `77.20.255.150`, kein globales IPv6 auf dem Host).
> Ziel: Zugriff **nur für bestimmte Clients**, kein öffentlicher Port,
> kein Router-Eingriff, kein VPS, kein Cloudflare.

Referenz aus Council-Entscheid: **C — Tailscale**, `serve` (privat),
niemals `funnel` (öffentlich). B (Cloudflare) nur Plan B, D (VPS) gestrichen.

```
Laptop/Handy (Tailscale-Client)
        | WireGuard + DERP (outbound, CGNAT-sicher)
        v
N150 tailscaled -> tailscale serve :443 -> http://127.0.0.1:8369
        v
t-bot-local-web-1 (Docker)
Postgres/Redis bleiben ohne Host-Ports (nur Docker-intern)
```

---

## 0. Voraussetzungen (5 Min)

- [ ] N150 läuft, `docker compose ps` zeigt `web healthy`
- [ ] Du hast ein Terminal mit `sudo` auf dem N150
- [ ] Tailscale-Konto (Google / Microsoft / GitHub / Apple) — kostenlos bis 100 Geräte
- [ ] Auf jedem Client (Laptop/Handy): Tailscale-App installierbar
- [ ] Kein Port-Forwarding nötig — Vodafone-Router wird **nicht** angefasst

Aktueller Ist-Stand bei dir (verifiziert):

```bash
docker ps --format '{{.Names}} {{.Ports}}'
# t-bot-local-web-1  0.0.0.0:8369->8369/tcp  <- aktuell welt-offen im LAN, muss weg
which tailscale || echo "noch nicht installiert"
tailscale status 2>&1 | head -5 || true
```

---

## 1. t-bot härten — VOR dem Tunnel (Pflicht, 10 Min)

Jeder Tunnel-Fehler ist sonst ein offener Bot. Genau diese Reihenfolge einhalten.

### 1.1 Welche Keys sind gesetzt (ohne Werte zu zeigen)

```bash
cd /home/kris/GITHUB/t-bot-lokal
grep -E '^(DEBUG|PASSPHRASE_GATE_ENABLED|DJANGO_ALLOWED_HOSTS|DJANGO_CSRF_TRUSTED_ORIGINS|RATE_LIMIT_TRUSTED_PROXIES|WEB_PORT)=' .env.local | sed 's/=.*/=***/'
ls -l .env.local   # muss -rw------- (0600) sein
```

Bei dir aktuell: nur `PASSPHRASE_GATE_ENABLED` + `WEB_PORT` gesetzt.
Es fehlen: `DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`.

### 1.2 `.env.local` härten

> Werte nie committen, nie in Logs kopieren. `SECRET_KEY`/`PASSPHRASE`/
> `POSTGRES_PASSWORD` bleiben unverändert — nur die Zeilen unten ergänzen/ändern.

```bash
cd /home/kris/GITHUB/t-bot-lokal
cp .env.local .env.local.bak-$(date +%F)
chmod 600 .env.local
```

Dann in `.env.local` editieren (Beispiel, Hostnamen unten anpassen):

```ini
DEBUG=False
PASSPHRASE_GATE_ENABLED=True
WEB_PORT=8369

# Nach `tailscale up` den echten Hostnamen eintragen, z.B.:
# DJANGO_ALLOWED_HOSTS=n150.tail-scale.ts.net,localhost,127.0.0.1
# DJANGO_CSRF_TRUSTED_ORIGINS=https://n150.tail-scale.ts.net
# Platzhalter bis dahin (nur Loopback, kein LAN):
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost

# Optional, aber empfohlen: nur verifizierte Proxy-Peers (Tailscale-IP des N150)
# RATE_LIMIT_TRUSTED_PROXIES=100.x.y.z/32
```

Danach Stack neu bauen + Health prüfen:

```bash
docker compose --env-file .env.local up --build -d
docker compose ps
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8369/health/
# erwartet: 200 (oder 302 auf /login/ bzw. /gate/ an der Startseite, /health/ muss 200 sein)
```

Wenn `400 Bad Request`: `DJANGO_ALLOWED_HOSTS` passt nicht zum aufgerufenen Host.
Wenn Redirect-Loop: `DJANGO_CSRF_TRUSTED_ORIGINS` fehlt das `https://tailscale-host`.

### 1.3 Exchange-Keys (Trading-Plane, oft vergessen)

- Börse: Subaccount nur mit **Trade-Recht, kein Withdraw**, IP-Allowlist geht hinter
  CGNAT/DERP **nicht** stabil — deshalb Key-Scope statt IP-Scope.
- Rotation/Revoke-Plan: wo widerrufst du den Key, wenn ein Client verloren geht?
- Tunnel schützt den Transport, nicht den Key-Missbrauch.

---

## 2. Tailscale auf dem N150 installieren (5 Min)

CachyOS/Arch (Paket `tailscale 1.102.3` in `cachyos-extra-v3` verifiziert):

```bash
sudo pacman -S --noconfirm tailscale
tailscale version
sudo systemctl enable --now tailscaled
systemctl is-active tailscaled
# erwartet: active
```

Kein Router-Eingriff, keine Firewall-Regel nötig — `tailscaled` baut nur
**ausgehende** UDP/443-Verbindungen zu DERP auf (DS-Lite-sicher).

Autostart prüfen:

```bash
systemctl is-enabled tailscaled
# erwartet: enabled
```

---

## 3. Ins Tailnet einloggen (2 Min, interaktiv)

```bash
sudo tailscale up
```

- Es erscheint eine URL → im Browser öffnen, mit deinem Tailscale-Konto einloggen.
- Danach:

```bash
tailscale status
tailscale ip -4   # z.B. 100.64.x.y — das ist deine private VPN-IP, kein Public-IP
tailscale ip -6   #  fd7a:... (ULA, nur im Tailnet)
```

Tailnet-Namen merken: `tailscale status` zeigt z.B. `n150.tail-scale.ts.net`.
Diesen Hostnamen in Schritt 1.2 in `DJANGO_ALLOWED_HOSTS` /
`DJANGO_CSRF_TRUSTED_ORIGINS` nachtragen + Stack neu starten.

2FA im Admin-Panel aktivieren: `https://login.tailscale.com/admin/settings/auth`
Verlorene Geräte sofort entfernen: `Admin → Machines → Remove`.

---

## 4. t-bot nur noch via Tailscale serven (3 Min)

### 4.1 Web auf Loopback verengen (empfohlen)

Aktuell lauscht `web` auf `0.0.0.0:8369` (jedes LAN-Gerät kann direkt ran).
Mit Tailscale brauchst du das nicht mehr. **Optional aber empfohlen:**

Datei `docker-compose.tailscale.override.yml` (liegt bereits im Repo):

```yaml
services:
  web:
    ports:
      - "127.0.0.1:${WEB_PORT:-8369}:8369"
```

Aktivieren:

```bash
cd /home/kris/GITHUB/t-bot-lokal
docker compose --env-file .env.local -f docker-compose.yml -f docker-compose.tailscale.override.yml up -d
ss -tlnp | grep 8369
# erwartet: nur noch 127.0.0.1:8369, kein 0.0.0.0:8369 mehr
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8369/health/
```

Rollback jederzeit:

```bash
docker compose --env-file .env.local up -d
```

> Hinweis: Danach geht `http://192.168.x.y:8369` im LAN **nicht** mehr direkt.
> Das ist Absicht — Zugriff nur noch via Tailscale (Schritt 4.2).

### 4.2 `serve` einrichten (privat) — NIEMALS `funnel`

```bash
# Privat im Tailnet auf 443 (richtig):
sudo tailscale serve --bg --https=443 http://127.0.0.1:8369

# Status prüfen:
sudo tailscale serve status
```

Erwartet: `https://n150.tail-scale.ts.net` → proxyt auf `127.0.0.1:8369`.
Zertifikat kommt automatisch von Tailscale (kein Let's Encrypt, kein Caddy nötig).

```bash
# GEFAHR — NIEMALS für t-bot ausführen (macht den Bot welt-öffentlich):
# sudo tailscale funnel --bg --https=443 http://127.0.0.1:8369
# Falls aus Versehen aktiv: sofort aus:
sudo tailscale funnel --https=443 off || true
sudo tailscale serve status
```

`serve` = nur Tailnet. `funnel` = Internet. Ein Flag Unterschied.

Persistenz: `serve` überlebt Reboots (Config in `/var/lib/tailscale/`),
trotzdem nach Reboot prüfen (siehe Schritt 7).

---

## 5. Clients einrichten (pro Gerät 2 Min)

1. Tailscale-App installieren (Windows/macOS/Linux/iOS/Android).
2. Mit **demselben** Tailnet-Login einloggen (oder per Invite/ACL, siehe 5.1).
3. Testen:

```
https://n150.tail-scale.ts.net/health/   -> 200
https://n150.tail-scale.ts.net/          -> Login/Gate (302 erwartet ohne Session)
```

Ohne Tailscale-Login muss die URL **nicht** erreichbar sein (Timeout/DNS-Fehler
ist korrekt — kein öffentlicher DNS).

### 5.1 Nur bestimmte Clients (ACL, empfohlen)

Default: jedes Gerät im Tailnet kann alles. Für „nur bestimmte Clients":

Admin → Access controls (`https://login.tailscale.com/admin/acls`):

```json
{
  "tagOwners": { "tag:tbot-viewer": ["autogroup:member"] },
  "acls": [
    { "action": "accept", "src": ["autogroup:member"], "dst": ["tag:tbot-viewer:443"] }
  ]
}
```

Dann am N150:

```bash
sudo tailscale up --advertise-tags=tag:tbot-viewer
```

Plus: Keys mit Ablauf (`Ephemeral` / `Expiry`), ungenutzte Geräte entfernen.
Jedes vergessene Handy = permanenter Zugang.

### 5.2 Alternative ohne Tailscale-Client: `serve` + Tailscale-Share

`tailscale share` teilt einzelne Geräte temporär — für Gäste ohne Vollzugriff.
Für Dauerbetrieb ungeeignet; dann lieber Plan B (Cloudflare Tunnel + Access).

---

## 6. Firewall auf dem N150 (CachyOS)

Mit Loopback-Bindung (Schritt 4.1) brauchst du **keine** 8369-Freigabe mehr.
Tailscale braucht nur Outbound (wird nicht geblockt).

Trotzdem prüfen, welche Firewall aktiv ist (nur eine verwalten):

```bash
systemctl is-active ufw firewalld nftables 2>/dev/null || true
sudo iptables -S 2>/dev/null | head -20 || true
sudo nft list ruleset 2>/dev/null | head -30 || true
```

Falls du `8369` früher in UFW/firewalld freigegeben hast, **wieder schließen**,
sobald 4.1 aktiv ist:

```bash
# UFW Beispiel (nur wenn UFW aktiv):
sudo ufw delete allow 8369/tcp || true
sudo ufw status numbered

# firewalld Beispiel (nur wenn aktiv):
sudo firewall-cmd --permanent --remove-port=8369/tcp || true
sudo firewall-cmd --reload
```

Docker-Hinweis: veröffentlichte Docker-Ports (`0.0.0.0:8369`) umgehen UFW-INPUT
via NAT. Deshalb ist `127.0.0.1:`-Bindung stärker als jede UFW-Regel.
Restriktive Docker-Policies gehören in `DOCKER-USER`, nicht in INPUT.

---

## 7. Dauerbetrieb + Verifikation

Nach jedem Reboot / Update:

```bash
systemctl is-active tailscaled
tailscale status
sudo tailscale serve status
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8369/health/
docker compose ps
```

Vom Client (mit Tailscale **an**):

```bash
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' https://n150.tail-scale.ts.net/health/
# erwartet: 200 (Hostnamen ersetzen)
```

Vom Fremdnetz **ohne** Tailscale muss dieselbe URL fehlschlagen (Timeout).
Wenn sie ohne Tailscale `200` liefert, läuft `funnel` — sofort `off` (Schritt 4.2).

Updates:

```bash
sudo pacman -Syu tailscale
sudo systemctl restart tailscaled
sudo tailscale serve status
```

---

## 8. Troubleshooting

| Symptom | Ursache / Fix |
|---|---|
| `tailscale up` hängt | Browser-Login nicht abgeschlossen; URL aus Terminal kopieren |
| `serve status` leer | `sudo tailscale serve --bg --https=443 http://127.0.0.1:8369` erneut, mit `sudo` |
| `400 Bad Request` im Browser | `DJANGO_ALLOWED_HOSTS` fehlt Tailnet-Host → Schritt 1.2 |
| CSRF-Fehler beim Login | `DJANGO_CSRF_TRUSTED_ORIGINS=https://<tailnet-host>` fehlt |
| `502 / Connection refused` | t-bot nicht healthy: `docker compose ps`, `docker logs t-bot-local-web-1 --tail 100` |
| Langsam (2-5s) | DERP-Relay statt Direktverbindung (normal hinter DS-Lite); für Bot-UI ok |
| `funnel` aus Versehen aktiv | `sudo tailscale funnel --https=443 off`, danach `serve status` |
| LAN `192.168.x.y:8369` geht nicht mehr | Korrekt nach Schritt 4.1 — nur noch via Tailnet-Host |
| Browser `DNS_PROBE_POSSIBLE`, aber `curl` geht | Browser nutzt eigenes Secure-DNS (DoH) und umgeht Tailscale-Split-DNS → siehe 8.1 |
| Exchange lehnt Order ab | Key-Scope prüfen (Trade ohne Withdraw), kein IP-Lock auf alte IPv4 |

### 8.1 Browser löst `*.ts.net` nicht auf (DNS_PROBE_POSSIBLE)

Symptom (verifiziert am 08.09.2026): `getent hosts`, `tailscale ping` und
`curl -vk https://<tailnet-host>/health/` liefern `200`, nur der Browser meldet
`DNS_PROBE_POSSIBLE`. Ursache: Secure-DNS / DNS-over-HTTPS im Browser schickt
Anfragen an einen öffentlichen Resolver statt an Tailscales Split-DNS
(`100.100.100.100`), wo `*.ts.net` allein existiert.

Fix:

- Firefox: Einstellungen → Datenschutz & Sicherheit → DNS-over-HTTPS → **Aus**.
- Chrome/Chromium: `chrome://settings/security` → „Sicheres DNS verwenden" → **Aus**.
  Danach Browser neu starten.
- Gegenprobe: `https://<tailnet-host>/health/` muss `{"status":"ok"}` liefern.
- Bleibt es dunkel: Browser-Proxy prüfen (aus), ggf. Gastprofil ohne Extensions testen.
  Referenz bleibt `curl` — geht `curl`, liegt jede weitere Differenz beim Browser.

DERP-Status (nur Diagnose, kein Fix nötig):

```bash
tailscale netcheck
# zeigt UDP/DERP-Erreichbarkeit hinter DS-Lite
```

---

## 9. Rollback / Deinstallation

```bash
# Serve entfernen, Tailnet verlassen, Dienst stoppen:
sudo tailscale serve --https=443 off || true
sudo tailscale logout
sudo systemctl disable --now tailscaled

# LAN-Zugriff zurück (Override entfernen):
cd /home/kris/GITHUB/t-bot-lokal
docker compose --env-file .env.local up -d
```

`.env.local.bak-*` aus Schritt 1.2 liegt als Backup daneben.

---

## 10. Alternativen (falls C nicht reicht)

- **Headscale / self-hosted Netbird**: gleiche Technik wie Tailscale, aber eigene
  Control-Plane ohne SaaS-Metadaten. Mehr Aufwand, erst sinnvoll wenn
  „kein Cloud-Leak" wörtlich auch Metadaten meint.
- **Plan B Cloudflare Tunnel**: nur wenn URL **ohne** VPN-Client Pflicht wird.
  Dann immer mit Zero-Trust Access (Email-PIN), niemals offen. Trading-Traffic
  läuft dann über Cloudflare-Edge (bewusst entscheiden).

---

## Checkliste „fertig"

- [ ] `.env.local` 0600, `DEBUG=False`, `GATE=True`, Tailnet-Host in
      `ALLOWED_HOSTS` + `CSRF_TRUSTED_ORIGINS`
- [ ] `/health/` lokal `200`
- [ ] `tailscaled` enabled + active
- [ ] `tailscale status` zeigt N150 + Client
- [ ] `serve status` zeigt `https -> 127.0.0.1:8369`, `funnel` ist aus
- [ ] Client via `https://<tailnet-host>/health/` → `200`
      (Browser: Secure-DNS/DoH aus, siehe 8.1)
- [ ] Ohne Tailscale ist dieselbe URL **nicht** erreichbar
- [ ] `0.0.0.0:8369` ist weg (`ss -tlnp`), nur noch `127.0.0.1:8369`
- [ ] 2FA + Geräte-Expiry + ACL geprüft
- [ ] Exchange-Keys ohne Withdraw-Recht
