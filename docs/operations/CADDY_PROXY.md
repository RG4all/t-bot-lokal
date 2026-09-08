# t-bot + Caddy: LAN-Zugriff über Port 8369 in Docker

## 1. Ziel und Referenzszenario

Dieses Manual beschreibt eine dauerhafte, reproduzierbare Konfiguration für einen lokalen `t-bot`, der in Docker läuft und über **Caddy als Reverse Proxy** aus dem LAN erreichbar ist.

### Referenzsystem

| Rolle | Adresse / Port |
|---|---|
| Server | `192.168.0.10` |
| Caddy auf dem Server | **TCP 8369** |
| t-bot Webcontainer | **TCP 8369, nur Docker-intern** |
| Beispiel-Client | `192.168.0.20` |
| Beispiel-Hostname | `tbot.local` |
| Docker-Proxy-Netz | `tbot_proxy` |
| t-bot internes Compose-Netz | `t-bot-local_default` |

### Zielpfad

```text
Client 192.168.0.20
        |
        | HTTPS/TCP 8369
        v
Server 192.168.0.10
        |
        | Caddy :8369
        v
   tbot_proxy
        |
        | http://tbot-web:8369
        v
 t-bot-local-web-1
        |
        | internes t-bot-Netz
        +---- PostgreSQL
        +---- Redis
        +---- Worker
```

**Wichtig:** Im Endzustand gibt es nur **einen Host-Port 8369**. Dieser gehört Caddy. Der t-bot-Container veröffentlicht seinen Port 8369 **nicht** auf dem Host. Docker Compose kann einen Dienst gleichzeitig an einem internen und einem externen Netzwerk betreiben; dafür ist das gemeinsame externe Proxy-Netz vorgesehen. citehttps://docs.docker.com/compose/how-tos/networking/

---

## 2. Warum die bisherige Konfiguration fehlschlug

Die bisherige Umgebung hatte mehrere widersprüchliche Betriebsmodelle:

```text
.env       -> WEB_PORT=8370
.env.local -> WEB_PORT=8369
```

und zeitweise:

```text
Host 8370 -> t-bot :8369
```

während die Diagnose weiterhin:

```text
Host 8369
```

prüfte.

Zusätzlich liefen Caddy und t-bot zunächst in unterschiedlichen Docker-Netzen:

```text
Caddy       -> nas-server_web
t-bot-web   -> t-bot-local_default
```

Dadurch konnte Caddy `tbot-web` nicht per Docker-DNS auflösen.

Der robuste Zielzustand ist deshalb:

```text
Caddy:
    Host :8369 -> Caddy :8369
                       |
                       +-> tbot_proxy -> tbot-web:8369

t-bot-web:
    container :8369
    KEIN Host-Port-Mapping
```

Docker empfiehlt für getrennte Compose-Projekte ein gemeinsames externes Netzwerk; die Dienste können darin über ihren Namen erreicht werden. Container-IP-Adressen sollen nicht fest konfiguriert werden, weil sie sich bei einer Neuerstellung ändern können. citehttps://docs.docker.com/compose/how-tos/networking/

---

# 3. Voraussetzungen

Auf dem Server `192.168.0.10`:

```text
Docker Engine
Docker Compose v2
Caddy als Docker-Container
T-Bot als Docker-Compose-Projekt
```

Prüfen:

### [bash]

```bash
docker version
docker compose version
docker ps
```

### [fish]

```fish
docker version
docker compose version
docker ps
```

Bash und Fish sind bei diesen Docker-Befehlen syntaktisch identisch.

---

# 4. Zielarchitektur der Docker-Netzwerke

Es werden zwei Netzwerke verwendet.

## 4.1 T-Bot-internes Netzwerk

```text
t-bot-local_default
```

Darin liegen:

- `t-bot-local-web-1`
- `t-bot-local-backtest-worker-1`
- `t-bot-local-postgres-1`
- `t-bot-local-redis-1`

## 4.2 Gemeinsames Proxy-Netz

```text
tbot_proxy
```

Darin liegen nur:

- `nas-server-proxy-1`
- `t-bot-local-web-1`

Das entspricht dem Docker-Hybridmodell: Der Webdienst hängt sowohl im internen Backend-Netz als auch im gemeinsam genutzten Proxy-Netz; Datenbank und Redis bleiben vom Proxy-Netz getrennt. citehttps://docs.docker.com/compose/how-tos/networking/

---

# 5. Gemeinsames Docker-Netzwerk anlegen

Dieser Schritt wird **einmal auf dem Server `192.168.0.10`** ausgeführt.

### [bash]

```bash
docker network inspect tbot_proxy >/dev/null 2>&1 || \
docker network create tbot_proxy
```

### [fish]

```fish
if not docker network inspect tbot_proxy >/dev/null 2>&1
    docker network create tbot_proxy
end
```

Prüfen:

### [bash] / [fish]

```bash
docker network inspect tbot_proxy
```

Das Netzwerk muss vor `docker compose up` vorhanden sein, wenn es als `external: true` definiert ist. citehttps://docs.docker.com/compose/how-tos/networking/

---

# 6. T-Bot Compose dauerhaft korrigieren

Datei:

```text
docker-compose.yml
```

Betroffener Abschnitt:

```text
services.web
```

## 6.1 Alte Konfiguration entfernen

Entfernen:

```yaml
ports:
  - "${WEB_PORT:-8369}:8369"
```

Dieses Mapping ist im Zielmodell falsch, weil dann der T-Bot selbst den Host-Port 8369 beansprucht.

## 6.2 Neue Konfiguration

Der `web`-Service soll stattdessen so aussehen:

```yaml
  web:
    build: .
    image: t-bot-local-app
    init: true

    environment:
      <<: *app-environment
      PORT: "8369"

    expose:
      - "8369"

    networks:
      default: {}
      tbot_proxy:
        aliases:
          - tbot-web

    volumes: *tuning-volume

    depends_on:
      tuner:
        condition: service_completed_successfully
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://localhost:8369/health/', timeout=2)",
        ]
      interval: 5s
      timeout: 5s
      retries: 30
      start_period: 30s
      start_interval: 2s

    mem_limit: 512m
    cpus: "${WEB_CPUS:-0.50}"
    restart: unless-stopped
```

Am Ende des Compose-Files:

```yaml
networks:
  tbot_proxy:
    external: true
    name: tbot_proxy
```

### Warum `expose` und nicht `ports`?

`expose: 8369` dokumentiert und stellt den Container-Port für die Docker-Netzwerke bereit, veröffentlicht ihn aber nicht auf dem Host. Der Host-Port gehört ausschließlich Caddy. Docker unterscheidet ausdrücklich zwischen dem Host-Port und dem Container-Port; Container-zu-Container-Kommunikation verwendet den Container-Port. citehttps://docs.docker.com/compose/how-tos/networking/

---

# 7. Caddy Compose dauerhaft korrigieren

Datei des Caddy-/NAS-Compose-Projekts:

```text
docker-compose.yml
```

Betroffener Service:

```text
services.proxy
```

Caddy muss zwei Docker-Netze kennen:

- das vorhandene `web`-Netz für die übrigen Dienste
- `tbot_proxy` für den T-Bot

## 7.1 Caddy-Port 8369 veröffentlichen

Der Caddy-Service benötigt zusätzlich:

```yaml
ports:
  - "8369:8369"
```

Falls Caddy weiterhin andere Dienste über 80/443 bedient, dürfen diese bestehenden Mappings natürlich erhalten bleiben. Für **t-bot selbst** ist aber nur der Host-Port 8369 relevant.

Beispiel:

```yaml
  proxy:
    image: caddy:latest
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "8369:8369"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - web
      - tbot_proxy
```

## 7.2 Externes Netzwerk deklarieren

Unter dem Service-Block:

```yaml
networks:
  web:
    driver: bridge

  tbot_proxy:
    external: true
    name: tbot_proxy
```

`external: true` bedeutet, dass Compose das Netzwerk nicht selbst verwaltet, sondern das bereits vorhandene Docker-Netz `tbot_proxy` verwendet. Genau dafür ist diese Funktion gedacht, wenn mehrere Compose-Projekte miteinander kommunizieren sollen. citehttps://docs.docker.com/reference/compose-file/networks/

---

# 8. Caddyfile für Port 8369

Datei:

```text
Caddyfile
```

Der T-Bot-Eintrag soll den **Caddy-Listener auf Port 8369** definieren.

## Empfohlene lokale HTTPS-Konfiguration

```caddyfile
tbot.local:8369 {
    tls internal

    encode zstd gzip

    reverse_proxy tbot-web:8369 {
        health_uri /health/
        health_interval 10s
        health_timeout 3s
    }
}
```

### Änderung gegenüber der bisherigen Konfiguration

Bisher:

```caddyfile
tbot.local {
    tls internal
    ...
}
```

Neu:

```caddyfile
tbot.local:8369 {
    tls internal
    ...
}
```

Der Upstream bleibt:

```text
tbot-web:8369
```

Caddy `reverse_proxy` unterstützt HTTP-Upstreams, Health Checks und konfigurierbare Transportparameter. citehttps://caddyserver.com/docs/caddyfile/directives/reverse_proxy

Caddy kann lokale Zertifikate mit seiner internen CA bereitstellen. Bei lokalen Hostnamen muss die Caddy-CA auf den Clients als vertrauenswürdig installiert werden, wenn Browser keine Zertifikatswarnung anzeigen sollen. citehttps://caddyserver.com/docs/quick-starts/reverse-proxy

---

# 9. Warum Caddy `tbot-web` erreichen kann

Caddy darf **nicht** verwenden:

```text
localhost:8369
127.0.0.1:8369
192.168.0.10:8369
```

für den Backend-Upstream.

Innerhalb des Caddy-Containers bedeutet `localhost` den Caddy-Container selbst.

Richtig ist:

```text
tbot-web:8369
```

weil `tbot-web` ein DNS-Alias des T-Bot-Webcontainers im gemeinsamen Docker-Netz ist.

Docker registriert Dienste im gemeinsamen Compose-Netzwerk per DNS und empfiehlt ausdrücklich die Verwendung von Servicenamen statt dynamischer Container-IP-Adressen. citehttps://docs.docker.com/compose/how-tos/networking/

---

# 10. `.env` und `.env.local` bereinigen

Das Zielsystem benötigt keinen `WEB_PORT` mehr, weil der Host-Port 8369 Caddy gehört.

Auf dem T-Bot-Server:

### [bash]

```bash
grep -n '^WEB_PORT=' .env .env.local 2>/dev/null || true
```

### [fish]

```fish
grep -n '^WEB_PORT=' .env .env.local 2>/dev/null
```

Entfernen:

### [bash]

```bash
sed -i '/^WEB_PORT=/d' .env
sed -i '/^WEB_PORT=/d' .env.local
```

### [fish]

```fish
sed -i '/^WEB_PORT=/d' .env
sed -i '/^WEB_PORT=/d' .env.local
```

Kontrolle:

### [bash] / [fish]

```bash
grep -n '^WEB_PORT=' .env .env.local 2>/dev/null || true
```

bzw. in Fish:

```fish
grep -n '^WEB_PORT=' .env .env.local 2>/dev/null
```

Keine Ausgabe ist im Zielmodell korrekt.

---

# 11. Konfiguration vor dem Start prüfen

Im T-Bot-Verzeichnis:

### [bash] / [fish]

```bash
docker compose config
```

Prüfen, dass `web`:

- keinen `ports:`-Block für 8369 mehr besitzt
- `expose: 8369` besitzt
- im `default`-Netz und `tbot_proxy` hängt
- den Alias `tbot-web` besitzt

Im Caddy-Compose-Projekt ebenfalls:

```bash
docker compose config
```

Dort muss der Caddy-Service `tbot_proxy` und `8369:8369` enthalten.

---

# 12. T-Bot neu starten

Im T-Bot-Projekt:

### [bash]

```bash
cd /GITHUB/t-bot-lokal
docker compose down
docker compose up -d --build
docker compose ps
```

### [fish]

```fish
cd /GITHUB/t-bot-lokal
docker compose down
docker compose up -d --build
docker compose ps
```

Der Webcontainer darf jetzt **keinen Host-Port 8369** anzeigen.

Erwartet:

```text
t-bot-local-web-1   Up ... (healthy)
```

ohne:

```text
0.0.0.0:8369->8369/tcp
```

---

# 13. T-Bot intern prüfen

### [bash] / [fish]

```bash
docker exec t-bot-local-web-1 \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8369/health/', timeout=3).status)"
```

Erwartet:

```text
200
```

Danach Netzwerke prüfen:

```bash
docker inspect t-bot-local-web-1 \
  --format '{{range $name, $conf := .NetworkSettings.Networks}}{{println $name}}{{end}}'
```

Erwartet:

```text
t-bot-local_default
tbot_proxy
```

---

# 14. Caddy neu starten

Im Caddy-/NAS-Projekt:

### [bash]

```bash
docker compose up -d --force-recreate proxy
```

### [fish]

```fish
docker compose up -d --force-recreate proxy
```

Prüfen:

```bash
docker inspect nas-server-proxy-1 \
  --format '{{range $name, $conf := .NetworkSettings.Networks}}{{println $name}}{{end}}'
```

Erwartet:

```text
nas-server_web
tbot_proxy
```

---

# 15. Docker-DNS und Backend-Verbindung testen

Das ist der wichtigste interne Test.

### DNS

```bash
docker exec nas-server-proxy-1 getent hosts tbot-web
```

Fish identisch:

```fish
docker exec nas-server-proxy-1 getent hosts tbot-web
```

Erwartet wird eine Adresse aus dem Docker-Netz, z. B.:

```text
172.22.x.x tbot-web
```

Die konkrete IP darf sich ändern. Der Name `tbot-web` muss stabil bleiben.

### HTTP zum Backend

```bash
docker exec nas-server-proxy-1 \
  curl -fsS -v http://tbot-web:8369/health/
```

Erwartet:

```text
HTTP/1.1 200 OK
```

Wenn dieser Test fehlschlägt, ist **noch kein Browser-Test sinnvoll**. Dann liegt das Problem zwischen Caddy und dem T-Bot-Docker-Netz.

---

# 16. Caddy-Konfiguration validieren und laden

### [bash] / [fish]

```bash
docker exec nas-server-proxy-1 \
  caddy validate --config /etc/caddy/Caddyfile
```

Dann:

```bash
docker exec nas-server-proxy-1 \
  caddy reload --config /etc/caddy/Caddyfile
```

Caddy dokumentiert `reload` als normalen Weg, eine geänderte Caddyfile-Konfiguration ohne unnötigen Container-Neustart zu übernehmen. citehttps://caddyserver.com/docs/quick-starts/reverse-proxy

---

# 17. Host-Port 8369 verifizieren

Auf `192.168.0.10`:

### [bash]

```bash
sudo ss -ltnp 'sport = :8369'
```

### [fish]

```fish
sudo ss -ltnp 'sport = :8369'
```

Es muss genau **Caddy/Docker** auf 8369 geben.

Zusätzlich:

```bash
docker port nas-server-proxy-1
```

Erwartet:

```text
8369/tcp -> 0.0.0.0:8369
8369/tcp -> [::]:8369
```

Der T-Bot darf dagegen keinen Host-Port 8369 veröffentlichen:

```bash
docker port t-bot-local-web-1
```

Im Zielmodell sollte dort **keine Ausgabe** erscheinen.

---

# 18. Lokalen Zugriff auf dem Server testen

Mit HTTP:

```bash
curl -v http://192.168.0.10:8369/health/
```

Wenn das Caddyfile HTTPS auf `tbot.local:8369` verwendet, ist der korrekte Test:

```bash
curl -vk https://tbot.local:8369/health/
```

Für den Namen muss `tbot.local` auf `192.168.0.10` zeigen.

---

# 19. `/etc/hosts` für den Client konfigurieren

Wenn kein lokaler DNS-Server den Namen `tbot.local` auflöst, wird der Name auf jedem Client manuell eingetragen.

## 19.1 Server `192.168.0.10`

Optional, aber sinnvoll:

```text
192.168.0.10 tbot.local
```

## 19.2 Client `192.168.0.20`

Auf dem Client:

```text
192.168.0.10 tbot.local
```

Datei:

```text
/etc/hosts
```

### [bash]

```bash
echo '192.168.0.10 tbot.local' | sudo tee -a /etc/hosts
```

### [fish]

```fish
echo '192.168.0.10 tbot.local' | sudo tee -a /etc/hosts
```

Bash und Fish sind hier identisch.

Prüfen:

```bash
getent hosts tbot.local
```

Erwartet:

```text
192.168.0.10 tbot.local
```

`/etc/hosts` wird auf dem **Client** benötigt, wenn der Client keinen DNS-Eintrag für `tbot.local` hat. Ein Eintrag auf dem Server allein löst das Problem auf anderen PCs nicht.

---

# 20. Browser-Test vom Client `192.168.0.20`

Bei der oben empfohlenen Konfiguration:

```text
https://tbot.local:8369/
```

Healthcheck:

```text
https://tbot.local:8369/health/
```

Bei lokalem TLS mit `tls internal` muss der Client der Caddy-Root-CA vertrauen. Ein einzelner `curl -k`-Test ist nur ein Diagnoseverfahren und keine produktive Zertifikatslösung.

---

# 21. Netzwerk ohne Browser testen

Auf `192.168.0.20`:

### TCP-Port

```bash
nc -vz 192.168.0.10 8369
```

Falls `nc` nicht installiert ist:

```bash
curl -vk --connect-timeout 5 https://tbot.local:8369/health/
```

### Route

```bash
ip route get 192.168.0.10
```

### DNS/hosts

```bash
getent hosts tbot.local
```

---

# 22. Firewall: zuerst die aktive Firewall identifizieren

Auf dem Server `192.168.0.10`:

```bash
systemctl is-active ufw 2>/dev/null || true
systemctl is-active firewalld 2>/dev/null || true
systemctl is-active nftables 2>/dev/null || true
```

Zusätzlich:

```bash
sudo nft list ruleset
sudo iptables -S
sudo ip6tables -S
```

Nicht mehrere Firewall-Frameworks gleichzeitig konfigurieren, ohne zu wissen, welches die aktive Policy besitzt.

---

# 23. UFW

UFW ist vor allem auf Ubuntu/Debian verbreitet. Die einfachste Freigabe ist Port 8369/TCP. citehttps://documentation.ubuntu.com/server/how-to/security/firewalls/

## 23.1 Nur LAN `192.168.0.0/24` erlauben

### [bash] / [fish]

```bash
sudo ufw allow from 192.168.0.0/24 to any port 8369 proto tcp
sudo ufw status numbered
```

Prüfen:

```bash
sudo ufw status verbose
```

## 23.2 Firewall aktivieren

Nur nach Prüfung der Regeln:

```bash
sudo ufw enable
```

**Vorsicht:** Vor `ufw enable` sicherstellen, dass SSH-Zugriff bereits erlaubt ist, falls der Server remote administriert wird.

## 23.3 Wichtiger Docker-Hinweis

Bei veröffentlichten Docker-Ports kann der Netzwerkverkehr durch Docker-NAT vor den normalen UFW-INPUT-Regeln umgeleitet werden. Docker dokumentiert diese Inkompatibilität ausdrücklich. Deshalb darf man sich bei einem veröffentlichten Docker-Port **nicht ausschließlich auf UFW verlassen**, wenn eine restriktive Quell-IP-Policy erforderlich ist. citehttps://docs.docker.com/engine/network/packet-filtering-firewalls/

Für das hier empfohlene Design ist die robustere Docker-spezifische Kontrolle im Abschnitt **iptables/DOCKER-USER** beschrieben.

---

# 24. firewalld

Auf Arch-/CachyOS-Systemen kann firewalld verwendet werden, sofern es installiert und aktiv ist.

Zuerst die aktive Zone ermitteln:

```bash
sudo firewall-cmd --get-active-zones
```

Beispiel für Zone `public`:

### [bash] / [fish]

```bash
sudo firewall-cmd --zone=public --add-port=8369/tcp
sudo firewall-cmd --permanent --zone=public --add-port=8369/tcp
sudo firewall-cmd --reload
```

Prüfen:

```bash
sudo firewall-cmd --zone=public --query-port=8369/tcp
sudo firewall-cmd --zone=public --list-ports
```

firewalld trennt Runtime- und permanente Konfiguration; für einen dauerhaften Port muss die Regel permanent gespeichert und anschließend geladen werden. citehttps://firewalld.org/documentation/howto/open-a-port-or-service.html

## 24.1 Nur LAN-Quelle zulassen

Für eine strengere Regel ist eine eigene Zone für die LAN-Quelle sauberer:

```bash
sudo firewall-cmd --permanent --new-zone=tbot-lan
sudo firewall-cmd --permanent --zone=tbot-lan --add-source=192.168.0.0/24
sudo firewall-cmd --permanent --zone=tbot-lan --add-port=8369/tcp
sudo firewall-cmd --reload
```

Die Source-Bindung einer Zone ist ein von firewalld unterstütztes Modell. citehttps://firewalld.org/documentation/man-pages/firewall-cmd.html

**Docker-Hinweis:** Docker integriert sich bei aktivem firewalld in eigene Docker-Zonen und Forwarding-Regeln. Deshalb muss bei einer restriktiven Docker-Port-Policy geprüft werden, ob die gewünschte Regel tatsächlich den veröffentlichten Port erreicht. Docker dokumentiert diese Integration ausdrücklich. citehttps://docs.docker.com/engine/network/packet-filtering-firewalls/

---

# 25. nftables

CachyOS verwendet typischerweise moderne Linux-Netzwerkwerkzeuge; wenn nftables direkt als Host-Firewall eingesetzt wird, muss zwischen **Host-INPUT** und **Docker-FORWARD/NAT** unterschieden werden.

## 25.1 Minimaler Host-Input für Port 8369

Beispiel für eine eigene Tabelle:

```nft
table inet tbot_host {
    chain input {
        type filter hook input priority 0;
        policy accept;

        ip saddr 192.168.0.0/24 tcp dport 8369 accept
    }
}
```

Ein solches Beispiel ist nur dann ausreichend, wenn die verwendete Docker-Firewall-Konfiguration den veröffentlichten Port nicht schon vorher über NAT/Forwarding behandelt.

## 25.2 Für Docker-Port-Restriktionen

Wenn Docker mit seinem nftables-Backend arbeitet, sollen **Docker-eigene Tabellen nicht direkt verändert werden**. Docker dokumentiert, dass diese Tabellen von Docker verwaltet werden und Änderungen verloren gehen können. Für eigene Regeln sollen eigene Tabellen/Base-Chains verwendet werden. citehttps://docs.docker.com/engine/network/firewall-nftables/

Beispiel für eine eigene Forward-Policy:

```nft
table inet tbot_docker_filter {
    chain forward {
        type filter hook forward priority -10;
        policy accept;

        ip saddr 192.168.0.0/24 tcp dport 8369 accept
    }
}
```

**Nicht blind installieren.** Ein produktiver nftables-Regelsatz muss in den vorhandenen Forwarding-Aufbau integriert werden. Vor Änderung immer sichern:

```bash
sudo nft list ruleset > /root/nftables-backup-before-tbot.conf
```

Fish:

```fish
sudo nft list ruleset > /root/nftables-backup-before-tbot.conf
```

### Docker-Backend prüfen

```bash
docker info | grep -i -E 'firewall|iptables|nftables'
```

Zusätzlich:

```bash
sudo nft list tables
```

Docker 29 unterstützt nftables als Firewall-Backend, dessen Unterstützung in Docker 29.x jedoch als experimentell dokumentiert ist. citehttps://docs.docker.com/engine/network/firewall-nftables/

---

# 26. iptables

Wenn Docker mit dem klassischen/iptables-Firewall-Backend arbeitet, ist `DOCKER-USER` der geeignete Ort für eigene Filterregeln, die vor den Docker-Regeln greifen. Docker dokumentiert diesen Mechanismus ausdrücklich. citehttps://docs.docker.com/engine/network/packet-filtering-firewalls/

## 26.1 LAN auf TCP 8369 beschränken

Zuerst Regelbestand sichern:

### [bash] / [fish]

```bash
sudo iptables-save > /root/iptables-before-tbot.rules
```

Dann:

```bash
sudo iptables -I DOCKER-USER 1 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
sudo iptables -I DOCKER-USER 2 -p tcp -s 192.168.0.0/24 --dport 8369 -j ACCEPT
sudo iptables -I DOCKER-USER 3 -p tcp --dport 8369 -j DROP
```

IPv6 analog:

```bash
sudo ip6tables-save > /root/ip6tables-before-tbot.rules
```

Wenn der Dienst **nur IPv4** benötigt, sollte IPv6 nicht zusätzlich freigegeben werden. Wenn IPv6 gewünscht ist, muss die Policy bewusst ergänzt werden.

Prüfen:

```bash
sudo iptables -L DOCKER-USER -n -v --line-numbers
```

Die Regeln bedeuten:

1. Bereits bestehende Verbindungen weiter zulassen.
2. Neue TCP-Verbindungen von `192.168.0.0/24` nach Port 8369 zulassen.
3. Andere TCP-Verbindungen nach Port 8369 verwerfen.

Die TCP-Destination-Port-Auswahl `--dport` und die Source-Auswahl sind Bestandteile der iptables-TCP-Erweiterungen. citehttps://man7.org/linux/man-pages/man8/iptables-extensions.8.html

## 26.2 Persistenz

Die konkrete Persistenzmethode hängt vom verwendeten Arch-/CachyOS-Setup ab. Nicht ungeprüft `iptables-save`-Dateien in ein nicht vorhandenes Init-System einbauen.

Zuerst prüfen:

```bash
systemctl is-enabled iptables.service 2>/dev/null || true
systemctl is-active iptables.service 2>/dev/null || true
```

Wenn auf dem Host ein anderes Firewall-Management die Regeln verwaltet, dieses bevorzugen.

---

# 27. Docker-Firewall-Regeln niemals löschen

Nicht ausführen:

```bash
sudo iptables -F
sudo iptables -t nat -F
sudo nft flush ruleset
```

wenn Docker produktiv läuft.

Docker erzeugt eigene Regeln für Bridge-Netze, NAT, Port-Publishing und Isolation. Diese Regeln sind Bestandteil des Docker-Netzwerkbetriebs. Docker warnt ausdrücklich davor, die eigenen Firewalltabellen direkt zu verändern. citehttps://docs.docker.com/engine/network/packet-filtering-firewalls/

---

# 28. Firewall-Variante auswählen

| System | Geeignet | Vorgehen |
|---|---:|---|
| UFW | Ja, einfache Host-Freigabe | TCP 8369 aus LAN erlauben; Docker-Portrestriktion separat beachten |
| firewalld | Ja | Zone/Source konfigurieren; Docker-Integration beachten |
| iptables | **Sehr gut für Docker mit iptables-Backend** | `DOCKER-USER` verwenden |
| nftables | Ja, aber sorgfältig | Eigene Base-Chain, Docker-Tabellen nicht verändern |
| Keine Host-Firewall | Technisch möglich, nicht empfohlen | Router/AP-Isolation und LAN-Vertrauen vorausgesetzt |

---

# 29. Netzwerkzugriff vom Client testen

Auf `192.168.0.20` zuerst ICMP:

```bash
ping -c 3 192.168.0.10
```

Fish identisch:

```fish
ping -c 3 192.168.0.10
```

Dann TCP 8369:

```bash
nc -vz 192.168.0.10 8369
```

Wenn `nc` nicht vorhanden ist:

```bash
curl -vk --connect-timeout 5 https://192.168.0.10:8369/health/
```

Bei `tbot.local`:

```bash
curl -vk --connect-timeout 5 https://tbot.local:8369/health/
```

---

# 30. Fehlerbilder eindeutig unterscheiden

## Fall A: `Connection refused`

Beispiel:

```text
connect to 192.168.0.10 port 8369 failed: Connection refused
```

Prüfen:

```bash
sudo ss -ltnp 'sport = :8369'
docker port nas-server-proxy-1
```

Es muss Caddy auf 8369 geben.

---

## Fall B: Timeout

Beispiel:

```text
Connection timed out
```

Dann zuerst:

```bash
ping -c 3 192.168.0.10
nc -vz 192.168.0.10 8369
```

Anschließend Firewall prüfen.

Wenn `ping` funktioniert und `8369` nicht, liegt das Problem meistens bei Firewall, Interface-Bindung oder Docker-Port-Publishing.

---

## Fall C: Caddy antwortet, aber `502 Bad Gateway`

Server:

```bash
docker exec nas-server-proxy-1 \
  curl -fsS http://tbot-web:8369/health/
```

Wenn dieser Test fehlschlägt:

```bash
docker network inspect tbot_proxy
docker inspect t-bot-local-web-1
docker logs --tail=200 nas-server-proxy-1
docker logs --tail=200 t-bot-local-web-1
```

Dann liegt das Problem zwischen Caddy und t-bot.

---

## Fall D: Caddy liefert Zertifikatsfehler

Test:

```bash
curl -vk https://tbot.local:8369/health/
```

Wenn `curl -k` funktioniert, aber der Browser nicht, ist der Netzwerkweg grundsätzlich in Ordnung. Dann fehlt dem Client das Vertrauen in die Caddy-interne CA.

---

## Fall E: `Could not resolve host: tbot-web`

Server:

```bash
docker exec nas-server-proxy-1 getent hosts tbot-web
```

Wenn keine Auflösung erfolgt:

```bash
docker network inspect tbot_proxy
```

Beide Container müssen dort erscheinen:

```text
nas-server-proxy-1
t-bot-local-web-1
```

---

## Fall F: `tbot.local` wird auf dem Client nicht gefunden

Client:

```bash
getent hosts tbot.local
```

Wenn keine Ausgabe kommt, `/etc/hosts` prüfen:

```text
192.168.0.10 tbot.local
```

---

# 31. Browser-/HSTS-Probleme

Bei der empfohlenen HTTPS-Konfiguration ist die URL:

```text
https://tbot.local:8369/
```

Nicht:

```text
http://localhost:8369/
```

und nicht:

```text
https://localhost:8369/
```

Die Kombination aus Hostname, Port und Zertifikat muss zusammenpassen.

Für einen isolierten Browser-Test:

### Chromium

```bash
chromium --user-data-dir=/tmp/tbot-browser-test 'https://tbot.local:8369/'
```

Fish:

```fish
chromium --user-data-dir=/tmp/tbot-browser-test 'https://tbot.local:8369/'
```

Damit wird ein frisches Browserprofil verwendet.

---

# 32. Django-/T-Bot-Hostnamen hinter Caddy

Falls Django den Hostnamen ablehnt, muss die Anwendung den verwendeten Host akzeptieren.

Typische Werte sind:

```python
ALLOWED_HOSTS = [
    "tbot.local",
    "192.168.0.10",
    "localhost",
    "127.0.0.1",
]
```

Für CSRF bei HTTPS:

```python
CSRF_TRUSTED_ORIGINS = [
    "https://tbot.local:8369",
]
```

Falls TLS an Caddy endet:

```python
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

Diese Werte müssen mit der vorhandenen Django-Konfiguration des Projekts abgeglichen werden. Sie sollten nicht blind bestehende Security-Einstellungen überschreiben.

---

# 33. Dauerbetrieb: Startreihenfolge und Persistenz

Die gewünschte Reihenfolge ist:

```text
Docker
  |
  +-- tbot_proxy vorhanden
  |
  +-- t-bot PostgreSQL / Redis
  |
  +-- t-bot web
  |
  +-- Caddy
  |
  +-- Clientzugriff
```

Docker Compose sorgt bei korrekter `depends_on`-Konfiguration für die Startabhängigkeiten innerhalb des T-Bot-Projekts. Das externe `tbot_proxy` muss dagegen bereits existieren, bevor Caddy und t-bot hochfahren. citehttps://docs.docker.com/compose/how-tos/networking/

### Server-Startprüfung

```bash
docker network inspect tbot_proxy >/dev/null
docker ps
docker compose ps
```

Dann:

```bash
docker exec nas-server-proxy-1 curl -fsS http://tbot-web:8369/health/
```

Erst wenn dieser Test `200` liefert, ist die interne Proxy-Kette vollständig aktiv.

---

# 34. Produktions-Checkliste

- [ ] Server hat `192.168.0.10`.
- [ ] Client hat `192.168.0.20`.
- [ ] Nur Caddy veröffentlicht Host-Port `8369`.
- [ ] t-bot-Webcontainer veröffentlicht **keinen** Host-Port.
- [ ] t-bot-Webcontainer lauscht intern auf `0.0.0.0:8369`.
- [ ] `t-bot-local-web-1` ist in `t-bot_proxy`.
- [ ] `nas-server-proxy-1` ist in `tbot_proxy`.
- [ ] `tbot-web` wird per Docker-DNS aufgelöst.
- [ ] `/health/` funktioniert aus dem Caddy-Container.
- [ ] Caddyfile verwendet `tbot-web:8369`.
- [ ] Caddy hört auf `:8369`.
- [ ] `/etc/hosts` oder DNS kennt `tbot.local -> 192.168.0.10`.
- [ ] Firewall erlaubt TCP 8369 aus `192.168.0.0/24`.
- [ ] Bei Docker-iptables-Backend sind restriktive Regeln in `DOCKER-USER` umgesetzt, falls nur LAN-Zugriff erlaubt sein soll.
- [ ] Caddy-Konfiguration ist validiert.
- [ ] Client erreicht `https://tbot.local:8369/health/`.
- [ ] Browser vertraut der Caddy-CA.
- [ ] `.env` und `.env.local` enthalten keine konkurrierenden `WEB_PORT`-Definitionen.

---

# 35. Referenzkonfiguration

## T-Bot `docker-compose.yml`

Wesentliche produktive Struktur:

```yaml
services:
  web:
    build: .
    image: t-bot-local-app
    init: true
    environment:
      <<: *app-environment
      PORT: "8369"
    expose:
      - "8369"
    networks:
      default: {}
      tbot_proxy:
        aliases:
          - tbot-web
    volumes: *tuning-volume
    depends_on:
      tuner:
        condition: service_completed_successfully
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://localhost:8369/health/', timeout=2)",
        ]
      interval: 5s
      timeout: 5s
      retries: 30
      start_period: 30s
      start_interval: 2s
    restart: unless-stopped

networks:
  tbot_proxy:
    external: true
    name: tbot_proxy
```

## Caddy Compose

```yaml
services:
  proxy:
    image: caddy:latest
    restart: unless-stopped
    ports:
      - "8369:8369"
      # Bereits vorhandene Ports für andere Caddy-Dienste können zusätzlich bestehen.
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - web
      - tbot_proxy

networks:
  web:
    driver: bridge
  tbot_proxy:
    external: true
    name: tbot_proxy
```

## Caddyfile

```caddyfile
tbot.local:8369 {
    tls internal

    encode zstd gzip

    reverse_proxy tbot-web:8369 {
        health_uri /health/
        health_interval 10s
        health_timeout 3s
    }
}
```

---

# 36. Vollständiger Verifikationstest

## Server `192.168.0.10`

```bash
sudo ss -ltnp 'sport = :8369'
docker port nas-server-proxy-1
docker port t-bot-local-web-1
docker network inspect tbot_proxy
docker exec nas-server-proxy-1 getent hosts tbot-web
docker exec nas-server-proxy-1 curl -fsS http://tbot-web:8369/health/
curl -vk https://tbot.local:8369/health/
```

Fish:

```fish
sudo ss -ltnp 'sport = :8369'
docker port nas-server-proxy-1
docker port t-bot-local-web-1
docker network inspect tbot_proxy
docker exec nas-server-proxy-1 getent hosts tbot-web
docker exec nas-server-proxy-1 curl -fsS http://tbot-web:8369/health/
curl -vk https://tbot.local:8369/health/
```

## Client `192.168.0.20`

```bash
getent hosts tbot.local
nc -vz 192.168.0.10 8369
curl -vk https://tbot.local:8369/health/
```

Fish:

```fish
getent hosts tbot.local
nc -vz 192.168.0.10 8369
curl -vk https://tbot.local:8369/health/
```

Wenn alle drei Tests erfolgreich sind, ist die komplette Kette funktionsfähig:

```text
192.168.0.20
   |
   | TCP/HTTPS 8369
   v
192.168.0.10
   |
   | Caddy :8369
   v
 tbot_proxy
   |
   | Docker DNS: tbot-web
   v
 t-bot-web :8369
   |
   v
 /health/ -> 200
```

---

# 37. Troubleshooting-Flussdiagramm

```text
                         START
                           |
                           v
              +--------------------------+
              | Server 192.168.0.10      |
              | Caddy auf TCP 8369?      |
              +-------------+------------+
                            |
                nein        |        ja
                 |          v
                 |   +----------------------+
                 |   | `ss :8369` und       |
                 |   | `docker port Caddy`  |
                 |   +----------+-----------+
                 |              |
                 |              v
                 |   +----------------------+
                 |   | Port 8369 erreichbar |
                 |   | lokal?               |
                 |   +----------+-----------+
                 |              |
                 |       nein   |   ja
                 |        |     | 
                 |        v     v
                 |   Firewall / Docker   +-----------------------+
                 |   Port Publishing     | Caddy -> tbot-web     |
                 |                       | per Docker-DNS?       |
                 |                       +-----------+-----------+
                 |                                   |
                 |                            nein   |   ja
                 |                             |     |
                 |                             v     v
                 |                  +--------------+  +--------------------+
                 |                  | tbot_proxy   |  | /health/ via       |
                 |                  | prüfen       |  | Caddy = 200?       |
                 |                  +------+-------+  +---------+----------+
                 |                         |                    |
                 |                         |              nein  |  ja
                 |                         |               |    |
                 |                         v               v    v
                 |                  Beide Container     502 /    +-----------+
                 |                  ins gemeinsame     Caddy-    | LAN-      |
                 |                  Netz?              Logs      | Client?   |
                 |                                     prüfen    +-----+-----+
                 |                                             |
                 |                                      nein   |   ja
                 |                                       |     |
                 |                                       v     v
                 |                                  Client-  DONE
                 |                                  Firewall
                 |                                  / hosts /
                 |                                  Routing
                 |
                 +------------------------------------+
                                                      |
                                                      v
                                           +---------------------+
                                           | Firewall aktiv?     |
                                           +----------+----------+
                                                      |
                                           +----------+----------+
                                           |          |          |
                                          UFW      firewalld  nftables/iptables
                                           |          |          |
                                           v          v          v
                                      LAN-Regel   Zone/Source   Docker-Backend
                                      für 8369    für 8369      berücksichtigen
                                           |          |          |
                                           +----------+----------+
                                                      |
                                                      v
                                           +---------------------+
                                           | Client 192.168.0.20 |
                                           | TCP 8369 testen     |
                                           +----------+----------+
                                                      |
                                                nein  |  ja
                                                 |    |
                                                 v    v
                                        Routing,   DNS / hosts,
                                        Firewall   Zertifikat,
                                                   Browser prüfen
```

---

# 38. Kernregel für dieses Setup

Für dieses System gilt dauerhaft:

```text
HOST-PORT 8369
      |
      v
CADDY :8369
      |
      | tbot_proxy / Docker DNS
      v
tbot-web:8369
      |
      v
Django/Daphne
```

**Nicht gleichzeitig**:

```text
Caddy      -> Host :8369
T-Bot      -> Host :8369
```

Das wäre ein echter Portkonflikt.

Der t-bot-Port `8369` bleibt im Container unverändert. Nur Caddy besitzt den Host-Port `8369`. Die Trennung zwischen Host-Port und Container-Port ist ein grundlegendes Docker-Port-Publishing-Prinzip. citehttps://docs.docker.com/engine/network/port-publishing/

---

# 39. Quellen und technische Referenzen

- Docker Compose Networking: https://docs.docker.com/compose/how-tos/networking/
- Docker Compose Networks: https://docs.docker.com/reference/compose-file/networks/
- Docker Port Publishing: https://docs.docker.com/engine/network/port-publishing/
- Docker Packet Filtering / Firewalls: https://docs.docker.com/engine/network/packet-filtering-firewalls/
- Docker nftables: https://docs.docker.com/engine/network/firewall-nftables/
- Caddy `reverse_proxy`: https://caddyserver.com/docs/caddyfile/directives/reverse_proxy
- Caddy Reverse Proxy Quick Start: https://caddyserver.com/docs/quick-starts/reverse-proxy
- UFW / Ubuntu Firewall: https://documentation.ubuntu.com/server/how-to/security/firewalls/
- firewalld `firewall-cmd`: https://firewalld.org/documentation/man-pages/firewall-cmd.html
- firewalld Port-Freigabe: https://firewalld.org/documentation/howto/open-a-port-or-service.html
- nftables Referenz: https://wiki.nftables.org/wiki-nftables/index.php/Quick_reference-nftables_in_10_minutes
- iptables TCP-Erweiterungen: https://man7.org/linux/man-pages/man8/iptables-extensions.8.html

