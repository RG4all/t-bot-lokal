import logging
from decimal import Decimal

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


class Configuration(models.Model):
    MARKET_CHOICES = (("spot", "Spot"), ("futures", "Futures"))
    EXCHANGE_CHOICES = (
        ("binance", "Binance"),
        ("bingx", "BingX"),
        ("bybit", "Bybit"),
        ("bitmart", "BitMart"),
        ("bitunix", "Bitunix"),
    )

    is_running = models.BooleanField(
        default=False,
        verbose_name="Bot läuft",
        help_text="Gibt an, ob der Bot aktuell laufen soll.",
    )
    name = models.CharField(
        max_length=100,
        verbose_name="Name der Konfiguration",
        help_text="Frei wählbare Bezeichnung (mindestens 3 Zeichen), z. B. „Binance Spot Top 5“.",
    )
    market = models.CharField(
        max_length=20,
        choices=MARKET_CHOICES,
        default="spot",
        verbose_name="Marktart",
        help_text="Handelsmarkt: Spot (Kassamarkt) oder Futures (Derivate/Swaps).",
    )
    sales_stop_threshold = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name="Gesamtverlustgrenze / Sales Stop (%)",
        help_text="Maximaler Verlust des Gesamtkapitals in Prozent (0 = deaktiviert).",
    )
    countdown_reset_indicators = models.BooleanField(
        default=False,
        verbose_name="Indikatoren nach Verkauf zurücksetzen",
        help_text="Indikatoren und 10-Punkte-Preisbuffer nach einem Verkauf zurücksetzen.",
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    exchange = models.CharField(
        max_length=20,
        choices=EXCHANGE_CHOICES,
        default="binance",
        verbose_name="Börse (Exchange)",
        help_text="Kryptobörse für Marktdaten und Paper-Trading: Binance, BingX, Bybit, BitMart oder Bitunix.",
    )
    symbols = models.CharField(
        max_length=200,
        default="BTC/USDT,ETH/USDT,SOL/USDT,LTC/USDT,XRP/USDT",
        verbose_name="Handelspaare (Symbole)",
        help_text="Kommagetrennte CCXT-Symbole (z. B. BTC/USDT, ETH/USDT).",
    )
    start_capital = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=Decimal(100),
        validators=[MinValueValidator(Decimal("0.00000001"))],
        verbose_name="Startkapital (USDT)",
        help_text="Virtuelles Anfangskapital für die Paper-Trading-Simulation.",
    )
    trade_amount = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=Decimal(10),
        validators=[MinValueValidator(Decimal("0.00000001"))],
        verbose_name="Trade-Betrag pro Position (USDT)",
        help_text="Virtueller Nominalbetrag je Position. Kaufgebühr muss zusätzlich gedeckt sein.",
    )
    take_profit = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("0.5"),
        validators=[MinValueValidator(Decimal("0.001")), MaxValueValidator(Decimal(100))],
        verbose_name="Take Profit (%)",
        help_text="Prozentualer Kursgewinn ab Einstieg zum automatischen Verkauf.",
    )
    stop_loss = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("0.5"),
        validators=[MinValueValidator(Decimal("0.001")), MaxValueValidator(Decimal(100))],
        verbose_name="Stop Loss (%)",
        help_text="Prozentualer Kursverlust ab Einstieg zum automatischen Verkauf.",
    )
    fee = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("0.1"),
        validators=[MinValueValidator(Decimal(0)), MaxValueValidator(Decimal(100))],
        verbose_name="Handelsgebühr je Order (%)",
        help_text="Simulierte Handelsgebühr je Kauf und Verkauf in Prozent.",
    )
    api_key = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        verbose_name="API-Key (optional)",
        help_text="Öffentlicher Börsenschlüssel. Für Paper-Trading nicht erforderlich.",
    )
    secret_key = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        verbose_name="Secret-Key (optional)",
        help_text="Geheimer Börsenschlüssel. Für Paper-Trading nicht erforderlich.",
    )
    countdown = models.IntegerField(
        default=1,
        validators=[MinValueValidator(0)],
        verbose_name="Start-Countdown (Minuten)",
        help_text="Wartezeit nach Bot-Start in Minuten, in der Kurse gesammelt aber noch keine Käufe ausgeführt werden.",
    )
    time_interval = models.IntegerField(
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(300)],
        verbose_name="Auswertungsintervall (Sekunden)",
        help_text="Pause zwischen zwei Preisprüfzyklen in Sekunden (1–300 s).",
    )
    div_DVA_prev_NDA_threshold_buy = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=0,
        verbose_name="Beschleunigung (DVA / prev NDA) – Kaufschwelle",
        help_text="Kaufschwelle für die relative Momentum-Beschleunigung (DVA / vorherige NDA). Im Backtesting als „Beschleunigung“ einstellbar.",
    )
    deltadelta_threshold_buy = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=0,
        verbose_name="DeltaDelta (geglättetes Momentum) – Kaufschwelle",
        help_text="Kaufschwelle für das geglättete Zwei-Punkt-Momentum ((NDA + vorherige NDA) / 2). Im Backtesting als „DeltaDelta“ einstellbar.",
    )
    nda_threshold_buy = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=0,
        verbose_name="NDA (normalisierte Preisänderung) – Kaufschwelle",
        help_text="Kaufschwelle für die prozentuale Preisänderung zum Vorpreis (((P0 - P1) / P1) * 100). Im Backtesting als „NDA“ einstellbar.",
    )

    def __str__(self):
        return self.name or f"Konfiguration {self.pk}"


class ErrorLog(models.Model):
    SEVERITY_CHOICES = (
        ("info", "Info"),
        ("warning", "Warnung"),
        ("error", "Fehler"),
        ("critical", "Kritisch"),
    )

    configuration = models.ForeignKey(
        Configuration,
        on_delete=models.CASCADE,
        related_name="error_logs",
        null=True,
        blank=True,
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default="error")
    source = models.CharField(max_length=100)
    exception_type = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["configuration", "resolved", "-timestamp"])]

    def __str__(self):
        return f"{self.timestamp} [{self.source}] {self.message[:80]}"


class TradingLog(models.Model):
    configuration = models.ForeignKey(
        Configuration,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    symbol = models.CharField(max_length=30)
    action = models.CharField(max_length=10)
    price = models.DecimalField(max_digits=20, decimal_places=8)
    amount = models.DecimalField(max_digits=20, decimal_places=8)
    fee_amount = models.DecimalField(max_digits=20, decimal_places=8)
    pl_nominal = models.DecimalField(max_digits=20, decimal_places=8)
    pl_relative = models.DecimalField(max_digits=20, decimal_places=8)
    total_pl = models.DecimalField(max_digits=20, decimal_places=8)
    current_capital = models.DecimalField(max_digits=20, decimal_places=8)
    tank = models.DecimalField(max_digits=20, decimal_places=8)
    order_id = models.CharField(max_length=120)

    class Meta:
        ordering = ["timestamp", "id"]
        indexes = [models.Index(fields=["configuration", "symbol", "timestamp"])]

    @property
    def date(self):
        return timezone.localtime(self.timestamp).date()

    @property
    def time(self):
        return timezone.localtime(self.timestamp).time()

    def __str__(self):
        return f"{self.timestamp} {self.symbol} {self.action}"


class DataLog(models.Model):
    configuration = models.ForeignKey(
        Configuration,
        on_delete=models.CASCADE,
        related_name="data_logs",
    )
    symbol = models.CharField(max_length=30)
    timestamp = models.DateTimeField(auto_now_add=True)
    price = models.DecimalField(max_digits=20, decimal_places=8)
    max_price = models.DecimalField(max_digits=20, decimal_places=8)
    min_price = models.DecimalField(max_digits=20, decimal_places=8)
    current_da = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    nda = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    prev_da = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    prev_nda = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    dva = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    deltadelta = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    div_DVA_prev_NDA = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        null=True,
        blank=True,
    )
    mvd = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal(0))

    class Meta:
        ordering = ["timestamp", "id"]
        indexes = [models.Index(fields=["configuration", "symbol", "timestamp"])]

    def __str__(self):
        return f"{self.timestamp} {self.symbol}"


class BacktestTask(models.Model):
    STATUS_CHOICES = (
        ("pending", "Ausstehend"),
        ("scheduled", "Geplant"),
        ("running", "Läuft"),
        ("paused", "Pausiert"),
        ("completed", "Abgeschlossen"),
        ("cancelled", "Abgebrochen"),
        ("failed", "Fehlgeschlagen"),
    )

    configuration = models.ForeignKey(Configuration, on_delete=models.CASCADE)
    symbol = models.CharField(max_length=200)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default="pending")
    progress = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    result = models.JSONField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    parameters = models.JSONField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    scheduled_start_time = models.DateTimeField(null=True, blank=True)
    is_scheduled = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "scheduled_start_time"])]

    def save(self, *args, **kwargs):
        if self.status in {"completed", "failed", "cancelled"} and not self.completed_at:
            self.completed_at = timezone.now()
        super().save(*args, **kwargs)

    def pause(self):
        if self.status == "running":
            self.status = "paused"
            self.save(update_fields=["status"])

    def resume(self):
        if self.status == "paused":
            self.status = "running"
            self.save(update_fields=["status"])

    def cancel(self):
        if self.status in {"scheduled", "running", "paused", "pending"}:
            self.status = "cancelled"
            self.is_scheduled = False
            self.save(update_fields=["status", "is_scheduled", "completed_at"])

    def update_progress(self, progress_percentage):
        progress = max(0, min(100, int(progress_percentage)))
        self.progress = progress
        self.save(update_fields=["progress"])

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        try:
            async_to_sync(channel_layer.group_send)(
                f"backtest_progress_{self.id}",
                {
                    "type": "backtest.progress",
                    "progress": progress,
                    "task_id": self.id,
                },
            )
        except Exception:
            logger.exception("Fortschritt für Backtest %s konnte nicht gesendet werden", self.id)
