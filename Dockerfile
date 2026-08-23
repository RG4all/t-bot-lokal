FROM python:3.12.7-slim-bookworm

# Lokale Container starten ohne vorgeschaltetes Gate; Produktion kann es per
# Environment-Variable wieder aktivieren.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PASSPHRASE_GATE_ENABLED=False \
    PORT=8369

WORKDIR /app

# Universelle Distro-abhaengige Installation (Container-Modus): installiert die
# fuer die Basis-Image-Distribution passenden Laufzeit-Pakete via apt/pacman/dnf.
# Das Skript ist so gebaut, dass es in Builds ohne Root-Privilegien oder
# ohne unterstuetzten Paketmanager nicht fehlschlaegt, sondern eine Warnung
# ausgibt - der Build faellt dann auf die explizite apt-get-Zeile zurueck.
COPY install.sh hardware-test.sh config.template ./
RUN chmod +x install.sh hardware-test.sh && \
    ./install.sh --mode=container --profile=runtime --yes || \
    ( echo "Fallback: install.sh konnte nicht vollstaendig laufen, nutze apt-get direkt" \
      && apt-get update \
      && apt-get install --no-install-recommends -y \
           fonts-dejavu-core \
           libharfbuzz-subset0 \
           libpango-1.0-0 \
           libpangoft2-1.0-0 \
      && rm -rf /var/lib/apt/lists/* )

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

RUN groupadd --system app && useradd --system --gid app --home /app app
COPY --chown=app:app . .
# WORKDIR legt /app als root an. Der unprivilegierte Runtime-Benutzer muss
# STATIC_ROOT (und ggf. lokale Cache-Verzeichnisse) darin anlegen duerfen.
RUN chmod +x /app/docker-entrypoint.sh /app/docker/*.sh /app/hardware-test.sh \
    && mkdir -p /tbot-runtime && chown app:app /tbot-runtime \
    && chown app:app /app
USER app

RUN SECRET_KEY=build-only-secret-key \
    PASSPHRASE=build-only-passphrase \
    DEBUG=False \
    python manage.py collectstatic --noinput

EXPOSE 8369
CMD ["/app/docker-entrypoint.sh"]
