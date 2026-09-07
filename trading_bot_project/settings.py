# trading_bot_project/settings.py
import logging
import os
import secrets as _secrets
from pathlib import Path

import dj_database_url

# ---------------------------------------------------------------------------
# Basisverzeichnis
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
APP_VERSION = (
    (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    if (BASE_DIR / "VERSION").exists()
    else "dev"
)

logger = logging.getLogger(__name__)


def env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default, minimum=1):
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        logger.warning("Ungültiger Integerwert für %s; verwende %s", name, default)
        return default


def env_float(name, default, minimum=0.1):
    try:
        return max(minimum, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        logger.warning("Ungültiger Zahlenwert für %s; verwende %s", name, default)
        return default


# ---------------------------------------------------------------------------
# Sicherheit / Grundkonfiguration
# ---------------------------------------------------------------------------
DEBUG = env_bool("DEBUG", default=not env_bool("RENDER", False))

# Signierte Cookie-Sessions brauchen einen privaten Schlüssel. Ein öffentlicher
# Entwicklungs-Key würde auch eine zufällige Gate-Passphrase wirkungslos machen.
# Für mehrere Prozesse und stabile Sessions über Neustarts SECRET_KEY setzen.
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY or not SECRET_KEY.strip():
    if env_bool("RENDER", False):
        raise RuntimeError(
            "SECRET_KEY environment variable is required on Render. "
            "Set it in the service's Environment settings."
        )
    if not DEBUG:
        raise RuntimeError("SECRET_KEY environment variable is required when DEBUG=False.")
    SECRET_KEY = _secrets.token_urlsafe(50)
    logger.warning(
        "SECRET_KEY nicht gesetzt. Temporärer Schlüssel generiert – "
        "Nur für lokale Entwicklung! Für stabile Sessions SECRET_KEY setzen."
    )

# Render stellt den öffentlichen Hostnamen automatisch als Env-Var bereit
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")

ALLOWED_HOSTS = ['tbot.local', 'localhost', '127.0.0.1', 't-bot-local-web-1']
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
extra_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "")
ALLOWED_HOSTS += [h.strip() for h in extra_hosts.split(",") if h.strip()]
if DEBUG:
    # WICHTIG: Niemals "*" verwenden – ermöglicht Host-Header-Injection,
    # Cache-Poisoning und CSRF-Bypass. Nur explizit lokale
    # Entwicklungshosts erlauben.
    ALLOWED_HOSTS += ["localhost", "127.0.0.1", "tbot.local", "[::1]"]

CSRF_TRUSTED_ORIGINS = ['http://tbot.local', 'https://tbot.local', 'http://tbot.local:8369', 'https://tbot.local:8369']

if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")
extra_origins = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS += [o.strip() for o in extra_origins.split(",") if o.strip()]

# ---------------------------------------------------------------------------
# Celery-Konfiguration
# ---------------------------------------------------------------------------
# Render Free Tier bietet keinen kostenlosen Redis/Broker und keine
# Background-Worker-Instanzen. Ist kein REDIS_URL gesetzt, laeuft Celery im
# "eager" Modus: app.task.delay(...) fuehrt die Aufgabe SOFORT UND SYNCHRON
# im aufrufenden Prozess aus - es wird weder ein Broker noch ein separater
# Worker-Prozess benoetigt. Sobald REDIS_URL gesetzt ist (z.B. Render Key
# Value oder ein externer Redis-Dienst auf einem bezahlten Plan), wird ganz
# normal ueber den Broker verteilt und ein "celery worker" Prozess kann die
# Tasks abarbeiten.
REDIS_URL = os.environ.get("REDIS_URL")

CELERY_TIMEZONE = "UTC"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 60
CELERY_TASK_TIME_LIMIT = 60 * 60 + 300
CELERY_WORKER_CONCURRENCY = 1
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1
CELERY_WORKER_MAX_MEMORY_PER_CHILD = env_int("CELERY_WORKER_MAX_MEMORY_PER_CHILD", 384_000)
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_ROUTES = {
    "trading.tasks.run_backtest": {"queue": "backtest", "priority": 0},
    "trading.tasks.simulate_candidate": {"queue": "backtest", "priority": 0},
    "trading.tasks.collect_results": {"queue": "backtest", "priority": 0},
    "trading.tasks.schedule_backtests": {"queue": "backtest", "priority": 0},
}

if REDIS_URL:
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL
    CELERY_TASK_ALWAYS_EAGER = False
else:
    CELERY_BROKER_URL = "memory://"
    CELERY_RESULT_BACKEND = "cache+memory://"
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

# Ein lokaler Backtest-Thread teilt CPU/RAM mit dem Bot und ist auf Render Free
# nicht sicher isolierbar. Lokal ist er für Entwicklung erlaubt; Produktion
# benötigt REDIS_URL plus separaten Celery-Worker.
BACKTEST_LOCAL_FALLBACK_ENABLED = env_bool(
    "BACKTEST_LOCAL_FALLBACK_ENABLED",
    default=not env_bool("RENDER", False),
)
BACKTEST_EXECUTION_AVAILABLE = bool(REDIS_URL) or BACKTEST_LOCAL_FALLBACK_ENABLED
BACKTEST_DEFAULT_PRICE_POINTS = min(
    5_000,
    env_int("BACKTEST_DEFAULT_PRICE_POINTS", 5_000, minimum=100),
)

# ---------------------------------------------------------------------------
# Security Header (nur wenn nicht DEBUG)
# ---------------------------------------------------------------------------
if not DEBUG:
    # Render terminiert TLS bereits am Edge/Loadbalancer und leitet Requests
    # per HTTP an den Container weiter, setzt aber den Header X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = "same-origin"
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

# MIME-Sniffing auch bei lokalem HTTP verhindern. Bewusst explizit und
# DEBUG-unabhängig; SecurityMiddleware erfasst auch Fehler und WhiteNoise-Antworten.
SECURE_CONTENT_TYPE_NOSNIFF = True

# ---------------------------------------------------------------------------
# Content-Security-Policy (CSP) – schützt vor XSS-Angriffen
# ---------------------------------------------------------------------------
# Alle Ressourcen werden lokal aus /static/ geladen – keine externen Domains.
# django-csp setzt den Content-Security-Policy Header automatisch.
CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = ("'self'",)
# Nur explizit markierte Template-Skripte dürfen inline ausgeführt werden.
CSP_INCLUDE_NONCE_IN = ("script-src",)
CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")
CSP_IMG_SRC = ("'self'", "data:")
CSP_FONT_SRC = ("'self'",)
CSP_CONNECT_SRC = ("'self'",)
CSP_FRAME_ANCESTORS = ("'self'",)
CSP_BASE_URI = ("'self'",)
CSP_FORM_ACTION = ("'self'",)

# Nur IPs/CIDRs eigener Reverse-Proxys eintragen, die X-Forwarded-For bereinigen.
# Leere Allowlist: ausschließlich die tatsächliche Peer-IP wird limitiert.
RATE_LIMIT_TRUSTED_PROXIES = [
    value.strip()
    for value in os.environ.get("RATE_LIMIT_TRUSTED_PROXIES", "").split(",")
    if value.strip()
]

LOGIN_URL = "/login/"
# Signierte Cookie-Sessions entkoppeln Login und Passphrase vom kurzzeitig
# nicht erreichbaren Free-Postgres. Inhalte sind signiert (nicht manipulierbar)
# und enthalten keine Exchange-Secrets.
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# CSRF-Cookie nicht über JavaScript (document.cookie) lesbar machen. Django
# liest es serverseitig; Templates und eigene Skripte nutzen {% csrf_token %}
# im Formularfeld statt Cookie-Zugriff. Zusätzliche Härtung, kein XSS-Schutz:
# Im DOM bleibt das Token für Skripte derselben Origin zugänglich.
# Bewusst nicht in den nicht-DEBUG-Block: HttpOnly funktioniert auch über
# plain HTTP und gehört in allen Umgebungen aktiviert.
CSRF_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# Apps / Middleware
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "csp",
    "trading.apps.TradingConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",
    "trading.middleware.DatabaseAvailabilityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "trading.middleware.PassphraseGateMiddleware",
    # Rate-Limiting für Auth-Endpunkte (Login, Gate, Register)
    # Muss NACH AuthenticationMiddleware sein
    "trading.rate_limit.RateLimitMiddleware",
]

ROOT_URLCONF = "trading_bot_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "trading.context_processors.app_metadata",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Channels (WebSocket) Konfiguration
# ---------------------------------------------------------------------------
# Render Free Tier laeuft immer nur mit EINER Instanz (kein Autoscaling im
# Free Plan), daher reicht der In-Memory Channel-Layer vollkommen aus und
# es wird kein Redis benoetigt. Wird REDIS_URL gesetzt (z.B. bei einem
# Upgrade auf einen bezahlten Plan mit mehreren Instanzen), wird automatisch
# auf den Redis-Channel-Layer umgeschaltet.
if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }

ASGI_APPLICATION = "trading_bot_project.asgi.application"
WSGI_APPLICATION = "trading_bot_project.wsgi.application"

# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------
# Zur Laufzeit wird Render PgBouncer (`connectionPoolString`) bevorzugt. Der
# direkte URL bleibt ausschließlich für Migrationen/Diagnose verfügbar.
DIRECT_DATABASE_URL = os.environ.get("DATABASE_URL")
DATABASE_POOL_URL = os.environ.get("DATABASE_POOL_URL")
USE_DIRECT_DATABASE_URL = env_bool("USE_DIRECT_DATABASE_URL", False)
DATABASE_URL = (
    DIRECT_DATABASE_URL if USE_DIRECT_DATABASE_URL or not DATABASE_POOL_URL else DATABASE_POOL_URL
)
if DATABASE_URL:
    database_config = dj_database_url.parse(
        DATABASE_URL,
        # Direkte Free-Postgres-Verbindungen am Request-/Task-Ende schließen,
        # damit die wenigen Server-Slots nie durch Thread-Locals belegt bleiben.
        # Ein optionaler bezahlter PgBouncer darf dagegen wiederverwendet werden.
        conn_max_age=60 if DATABASE_POOL_URL and not USE_DIRECT_DATABASE_URL else 0,
        conn_health_checks=True,
        ssl_require=env_bool("DATABASE_SSL_REQUIRE", True),
    )
    database_config.setdefault("OPTIONS", {}).update(
        {
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
            "tcp_user_timeout": 30_000,
            "application_name": "t-bot-web" if not USE_DIRECT_DATABASE_URL else "t-bot-migrate",
        }
    )
    # Nur ein explizit geprüfter Fallback wird verwendet. Das frühere Ableiten
    # eines externen Hosts wurde entfernt: Es verdoppelte fehlgeschlagene
    # Handshakes, wenn der Datastore selbst keine Slots mehr hatte.
    fallback_host = os.environ.get("DATABASE_FALLBACK_HOST", "").strip()
    if fallback_host and USE_DIRECT_DATABASE_URL:
        primary_host = database_config.get("HOST", "")
        database_config["HOST"] = f"{primary_host},{fallback_host}"
    DATABASES = {"default": database_config}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static Files (WhiteNoise - kein persistenter Storage auf Render noetig)
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Render Free Tier hat KEINEN persistenten Storage - Logdateien wuerden bei
# jedem Deploy/Neustart verloren gehen. Daher wird in Produktion nach STDOUT
# geloggt (von Render automatisch eingesammelt); lokal weiterhin zusaetzlich
# in eine Datei.
handlers = {
    "console": {
        "level": "INFO",
        "class": "logging.StreamHandler",
    },
}
trading_handlers = ["console"]

if DEBUG:
    handlers["file"] = {
        "level": "DEBUG",
        "class": "logging.FileHandler",
        "filename": os.path.join(BASE_DIR, "trading_bot.log"),
        "formatter": "verbose",
    }
    trading_handlers.append("file")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
    },
    "handlers": handlers,
    "loggers": {
        "trading": {
            "handlers": trading_handlers,
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# ---------------------------------------------------------------------------
# App-spezifische Einstellungen
# ---------------------------------------------------------------------------
# Steuert, ob TradingBots fuer aktive Konfigurationen beim Prozessstart
# automatisch gestartet werden sollen (siehe trading/apps.py).
AUTOSTART_BOTS = env_bool("AUTOSTART_BOTS", True)
DB_RECONNECT_MAX_RETRIES = env_int("DB_RECONNECT_MAX_RETRIES", 5, minimum=1)
DB_RECONNECT_BASE_DELAY = env_float("DB_RECONNECT_BASE_DELAY", 1.0)
DB_RECONNECT_MAX_DELAY = env_float("DB_RECONNECT_MAX_DELAY", 30.0)
DB_CIRCUIT_BREAKER_SECONDS = env_int("DB_CIRCUIT_BREAKER_SECONDS", 300, minimum=30)
BOT_DB_WORKERS = env_int("BOT_DB_WORKERS", 1, minimum=1)
MAX_DATA_LOGS_PER_SYMBOL = env_int("MAX_DATA_LOGS_PER_SYMBOL", 20_000, minimum=1_000)
DATA_LOG_CLEANUP_EVERY = env_int("DATA_LOG_CLEANUP_EVERY", 500, minimum=10)
DATA_LOG_WRITE_INTERVAL_SECONDS = env_int("DATA_LOG_WRITE_INTERVAL_SECONDS", 10, minimum=2)
BOT_CONFIG_REFRESH_SECONDS = env_int("BOT_CONFIG_REFRESH_SECONDS", 30, minimum=5)

# ---------------------------------------------------------------------------
# Passphrase-Gate (Landingpage vor Registrierung/Login)
# ---------------------------------------------------------------------------
PASSPHRASE = os.environ.get("PASSPHRASE")
if not PASSPHRASE or not PASSPHRASE.strip():
    if env_bool("RENDER", False):
        raise RuntimeError(
            "PASSPHRASE environment variable is required on Render. "
            "Generate it in the service environment settings."
        )
    if not DEBUG:
        raise RuntimeError("PASSPHRASE environment variable is required when DEBUG=False.")
    # Nur lokale Entwicklung: ein neuer Wert bei jedem Laden der Settings.
    # Mehrere Worker/Neustarts benötigen dieselbe explizite Environment-Variable.
    PASSPHRASE = _secrets.token_urlsafe(32)
    logger.warning(
        "PASSPHRASE nicht gesetzt. Generiert: %s – Nur für lokale Entwicklung!",
        PASSPHRASE,
    )
PASSPHRASE_GATE_ENABLED = env_bool("PASSPHRASE_GATE_ENABLED", True)
if not PASSPHRASE_GATE_ENABLED and (env_bool("RENDER", False) or not DEBUG):
    raise RuntimeError(
        "PASSPHRASE_GATE_ENABLED must be True on Render or when DEBUG=False."
    )
