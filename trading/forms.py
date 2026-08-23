import math
import re
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.models import User
from django.utils import timezone

from .backtest_templates import template_choices
from .market_data import MarketDataError, SymbolValidationError, validate_exchange_symbols
from .models import Configuration
from .strategy import (
    BOTH,
    DIRECTION_CHOICES,
    LONG,
    SHORT,
    liquidation_move_percent,
    max_leverage,
    resolve_leverage,
)

_SYMBOL_RE = re.compile(r"^[A-Z0-9._-]+/[A-Z0-9._:-]+$")
# Sicherheitsabsolute; das wirksame Limit wird zusätzlich pro Hardwareprofil
# und über die Environment-Konfiguration begrenzt.
_MAX_BACKTEST_COMBINATIONS = min(100_000, getattr(settings, "BACKTEST_MAX_COMBINATIONS", 20_000))
_MAX_BACKTEST_PRICE_POINTS = min(50_000, getattr(settings, "BACKTEST_MAX_PRICE_POINTS", 5_000))


def validate_leverage_and_direction(form, cleaned_data, exchange, market):
    """Prüft Hebel und Handelsrichtung gegen Markt und Börsenlimit.

    Regeln:
    * Spot kennt weder Hebel noch Short – beides wird klar abgewiesen.
    * Der Hebel darf den je Börse konfigurierten Maximalhebel nicht
      überschreiten (siehe ``EXCHANGE_MAX_LEVERAGE``).
    * Der Stop-Loss muss vor der Liquidationsschwelle greifen, sonst wäre
      die Margin bereits verloren, bevor der Stop überhaupt auslöst.
    """
    leverage = cleaned_data.get("leverage")
    direction = cleaned_data.get("trade_direction") or LONG
    stop_loss = cleaned_data.get("stop_loss")
    if not exchange or not market:
        return
    if market != "futures":
        if leverage not in (None, 1):
            form.add_error(
                "leverage",
                "Im Spot-Markt ist nur Hebel 1 möglich. Für Hebel bitte Futures wählen.",
            )
        if direction in {SHORT, BOTH}:
            form.add_error(
                "trade_direction",
                "Short-Positionen sind nur im Futures-Markt möglich.",
            )
        return
    limit = max_leverage(exchange)
    if leverage is not None and leverage > limit:
        form.add_error(
            "leverage",
            f"{dict(Configuration.EXCHANGE_CHOICES).get(exchange, exchange)} erlaubt in "
            f"dieser Installation höchstens Hebel {limit}x.",
        )
        return
    effective = resolve_leverage(exchange, market, leverage)
    if stop_loss is not None:
        liquidation = liquidation_move_percent(effective)
        if Decimal(str(stop_loss)) >= liquidation:
            form.add_error(
                "stop_loss",
                f"Bei Hebel {int(effective)}x wird die Position bereits ab "
                f"{liquidation:.2f} % Kursverlust liquidiert. Stop-Loss kleiner wählen.",
            )


class RegistrationForm(forms.ModelForm):
    password = forms.CharField(
        label="Passwort",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "Sicheres Passwort eingeben"}
        ),
        help_text="Mindestens 8 Zeichen, nicht nur Zahlen.",
    )
    confirm_password = forms.CharField(
        label="Passwort bestätigen",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "placeholder": "Passwort wiederholen"}
        ),
        help_text="Muss mit dem obigen Passwort übereinstimmen.",
    )

    class Meta:
        model = User
        fields = ["username", "email"]
        widgets = {
            "username": forms.TextInput(
                attrs={"autocomplete": "username", "placeholder": "z. B. trader1"}
            ),
            "email": forms.EmailInput(
                attrs={"autocomplete": "email", "placeholder": "name@example.com"}
            ),
        }
        labels = {
            "username": "Benutzername",
            "email": "E-Mail-Adresse",
        }
        help_texts = {
            "username": "Eindeutiger Benutzername für die Anmeldung.",
            "email": "Gültige E-Mail-Adresse für Benachrichtigungen und Accountverwaltung.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
            if field.help_text:
                field.widget.attrs["title"] = field.help_text

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        if password and password != cleaned_data.get("confirm_password"):
            self.add_error("confirm_password", "Passwörter stimmen nicht überein.")
        if password:
            try:
                password_validation.validate_password(password, self.instance)
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class LoginForm(forms.Form):
    username = forms.CharField(
        label="Benutzername",
        widget=forms.TextInput(
            attrs={"autocomplete": "username", "autofocus": True, "placeholder": "Benutzername"}
        ),
        help_text="Dein registrierter Benutzername.",
    )
    password = forms.CharField(
        label="Passwort",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password", "placeholder": "Passwort"}
        ),
        help_text="Dein Account-Passwort.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
            if field.help_text:
                field.widget.attrs["title"] = field.help_text


class ConfigurationForm(forms.ModelForm):
    class Meta:
        model = Configuration
        fields = [
            "name",
            "exchange",
            "market",
            "leverage",
            "trade_direction",
            "symbols",
            "start_capital",
            "trade_amount",
            "sales_stop_threshold",
            "take_profit",
            "stop_loss",
            "fee",
            "api_key",
            "secret_key",
            "countdown",
            "countdown_reset_indicators",
            "time_interval",
            "div_DVA_prev_NDA_threshold_buy",
            "deltadelta_threshold_buy",
            "nda_threshold_buy",
        ]
        labels = {
            "name": "Name der Konfiguration",
            "exchange": "Börse (Exchange)",
            "market": "Marktart",
            "leverage": "Hebel (nur Futures)",
            "trade_direction": "Handelsrichtung",
            "symbols": "Handelspaare (Symbole)",
            "start_capital": "Startkapital (USDT)",
            "trade_amount": "Trade-Betrag pro Position (USDT)",
            "sales_stop_threshold": "Gesamtverlustgrenze / Sales Stop (%)",
            "take_profit": "Take Profit (%)",
            "stop_loss": "Stop Loss (%)",
            "fee": "Handelsgebühr je Order (%)",
            "api_key": "API-Key (optional)",
            "secret_key": "Secret-Key (optional)",
            "countdown": "Start-Countdown (Minuten)",
            "countdown_reset_indicators": "Indikatoren nach Verkauf zurücksetzen",
            "time_interval": "Auswertungsintervall (Sekunden)",
            "div_DVA_prev_NDA_threshold_buy": "Beschleunigung (DVA / prev NDA) – Kaufschwelle",
            "deltadelta_threshold_buy": "DeltaDelta (geglättetes Momentum) – Kaufschwelle",
            "nda_threshold_buy": "NDA (normalisierte Preisänderung) – Kaufschwelle",
        }
        widgets = {
            "symbols": forms.Textarea(attrs={"rows": 3, "placeholder": "BTC/USDT, ETH/USDT"}),
            "api_key": forms.PasswordInput(attrs={"autocomplete": "off"}),
            "secret_key": forms.PasswordInput(attrs={"autocomplete": "off"}),
            "countdown_reset_indicators": forms.CheckboxInput(),
        }
        help_texts = {
            "name": "Frei wählbare Bezeichnung (mindestens 3 Zeichen), z. B. „Binance Spot Top 5“.",
            "exchange": "Kryptobörse für Marktdaten und Paper-Trading: Binance, BingX, Bybit, BitMart oder Bitunix.",
            "market": "Handelsmarkt: Spot (Kassamarkt) oder Futures (Derivate/Swaps).",
            "leverage": "Hebel für Futures (1 = ohne Hebel). Der Trade-Betrag ist die eingesetzte Margin, das Nominalvolumen beträgt Margin × Hebel. Im Spot-Markt wird immer mit Hebel 1 gerechnet.",
            "trade_direction": "Long (steigende Kurse), Short (fallende Kurse) oder beide Richtungen. Short setzt den Futures-Markt voraus.",
            "symbols": "Handelspaare durch Kommas trennen, z. B. BTC/USDT, ETH/USDT. Vorschläge richten sich nach Exchange und Markt.",
            "start_capital": "Virtuelles Anfangskapital für die Simulation in USDT.",
            "trade_amount": "Virtueller Nominalbetrag je Position. Kaufgebühr muss zusätzlich gedeckt sein.",
            "sales_stop_threshold": "Maximaler Gesamtverlust in % des Startkapitals, ab dem alle Trades gestoppt werden; 0 = deaktiviert.",
            "take_profit": "Prozentualer Kursgewinn ab Kaufkurs zum automatischen Verkauf mit Gewinn.",
            "stop_loss": "Prozentualer Kursverlust ab Kaufkurs zur automatischen Verlustbegrenzung.",
            "fee": "Simulierte Handelsgebühr je Order in Prozent, die beim Kauf und Verkauf anfällt.",
            "api_key": "Öffentlicher API-Schlüssel der Börse. Für Paper-Trading leer lassen.",
            "secret_key": "Geheimer API-Schlüssel der Börse. Für Paper-Trading leer lassen.",
            "countdown": "Wartezeit nach Bot-Start in Minuten, in der Kurse gesammelt aber noch keine Käufe ausgeführt werden.",
            "countdown_reset_indicators": "Leert den 10-Punkte-Preisbuffer nach einem Verkauf, damit Indikatoren für den nächsten Trade neu aufgebaut werden.",
            "time_interval": "Pause zwischen zwei Preisprüfzyklen in Sekunden (1–300 s). Für BitMart/Bitunix Spot min. 5 s.",
            "div_DVA_prev_NDA_threshold_buy": "Untere Schwelle für die Momentum-Beschleunigung (DVA / vorherige NDA). Im Backtesting als „Beschleunigung“ einstellbar.",
            "deltadelta_threshold_buy": "Untere Schwelle des geglätteten Zwei-Punkt-NDA-Momentums ((NDA + vorherige NDA) / 2). Im Backtesting als „DeltaDelta“ einstellbar.",
            "nda_threshold_buy": "Untere Schwelle der normalisierten prozentualen Preisänderung zum Vorpreis (((P0 - P1) / P1) * 100). Im Backtesting als „NDA“ einstellbar.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                css_class = "form-check-input"
            elif isinstance(field.widget, forms.Select):
                css_class = "form-select"
            else:
                css_class = "form-control"
            field.widget.attrs["class"] = " ".join(
                filter(None, (field.widget.attrs.get("class"), css_class))
            )
            if field.help_text:
                field.widget.attrs["title"] = field.help_text
        if self.instance and self.instance.pk:
            self.fields["api_key"].widget.attrs["placeholder"] = "Unverändert lassen"
            self.fields["secret_key"].widget.attrs["placeholder"] = "Unverändert lassen"
        # Rückwärtskompatibilität: Formulare und Integrationen, die Hebel und
        # Handelsrichtung nicht mitsenden, bleiben gültig und erhalten die
        # risikofreien Standardwerte (Hebel 1, Long).
        self.fields["leverage"].required = False
        self.fields["trade_direction"].required = False

    def clean_leverage(self):
        return self.cleaned_data.get("leverage") or 1

    def clean_trade_direction(self):
        return self.cleaned_data.get("trade_direction") or LONG

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()
                widget.attrs["aria-invalid"] = "true"

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if len(name) < 3:
            raise forms.ValidationError("Der Name muss mindestens 3 Zeichen lang sein.")
        return name

    def clean_symbols(self):
        raw_symbols = self.cleaned_data["symbols"]
        symbols = []
        for raw_symbol in raw_symbols.split(","):
            symbol = raw_symbol.strip().upper()
            if not symbol:
                continue
            if not _SYMBOL_RE.fullmatch(symbol):
                raise forms.ValidationError(
                    f"Ungültiges Handelspaar „{symbol}“. Erwartet wird z. B. BTC/USDT."
                )
            if symbol not in symbols:
                symbols.append(symbol)
        if not symbols:
            raise forms.ValidationError("Mindestens ein Handelspaar ist erforderlich.")
        if len(symbols) > 20:
            raise forms.ValidationError("Maximal 20 Handelspaare pro Konfiguration sind erlaubt.")
        normalized = ",".join(symbols)
        if len(normalized) > Configuration._meta.get_field("symbols").max_length:
            raise forms.ValidationError("Die Symbolliste ist zu lang.")
        return normalized

    def clean_api_key(self):
        value = self.cleaned_data.get("api_key")
        if not value and self.instance and self.instance.pk:
            return self.instance.api_key
        return value

    def clean_secret_key(self):
        value = self.cleaned_data.get("secret_key")
        if not value and self.instance and self.instance.pk:
            return self.instance.secret_key
        return value

    def clean(self):
        cleaned_data = super().clean()
        start_capital = cleaned_data.get("start_capital")
        trade_amount = cleaned_data.get("trade_amount")
        fee = cleaned_data.get("fee")
        api_key = cleaned_data.get("api_key")
        secret_key = cleaned_data.get("secret_key")
        interval = cleaned_data.get("time_interval")
        countdown = cleaned_data.get("countdown")
        loss_threshold = cleaned_data.get("sales_stop_threshold")

        if start_capital is not None and trade_amount is not None:
            fee_factor = 1 + ((fee or 0) / 100)
            if trade_amount * fee_factor > start_capital:
                self.add_error(
                    "trade_amount",
                    "Trade-Betrag einschließlich Kaufgebühr darf das Startkapital nicht überschreiten.",
                )
        if fee is not None and fee > 5:
            self.add_error("fee", "Eine simulierte Gebühr über 5 % ist nicht plausibel.")
        if bool(api_key) != bool(secret_key):
            message = "API-Key und Secret-Key müssen entweder beide gesetzt oder beide leer sein."
            self.add_error("api_key", message)
            self.add_error("secret_key", message)
        if interval is not None and interval > 300:
            self.add_error(
                "time_interval", "Das Zeitintervall darf höchstens 300 Sekunden betragen."
            )
        if countdown is not None and countdown > 10_080:
            self.add_error("countdown", "Der Countdown darf höchstens 7 Tage betragen.")
        if loss_threshold is not None and 0 < loss_threshold < 0.1:
            self.add_error(
                "sales_stop_threshold",
                "Die Gesamtverlustgrenze muss 0 (deaktiviert) oder mindestens 0,1 % sein.",
            )

        exchange = cleaned_data.get("exchange")
        market = cleaned_data.get("market")
        symbols_value = cleaned_data.get("symbols")
        validate_leverage_and_direction(self, cleaned_data, exchange, market)
        if (
            exchange in {"bitmart", "bitunix"}
            and market == "spot"
            and interval is not None
            and interval < 5
        ):
            self.add_error(
                "time_interval",
                "Für BitMart/Bitunix Spot sind mindestens 5 Sekunden erforderlich, "
                "damit Einzel-Ticker sicher unter dem API-Limit bleiben.",
            )
        validation_fields = ("exchange", "market", "symbols")
        validation_blocked = any(field in self.errors for field in validation_fields)
        if exchange and market and symbols_value and not validation_blocked:
            symbols = [symbol for symbol in symbols_value.split(",") if symbol]
            try:
                validate_exchange_symbols(exchange, market, symbols)
            except SymbolValidationError as exc:
                self.add_error("symbols", str(exc))
            except (MarketDataError, ValueError) as exc:
                self.add_error(
                    None,
                    f"Die Exchange-Konfiguration konnte nicht verifiziert werden: {exc}. "
                    "Bitte Verbindung, Exchange, Markt und Symbole prüfen und erneut speichern.",
                )
        return cleaned_data


class DashboardConfigurationForm(forms.ModelForm):
    class Meta:
        model = Configuration
        fields = [
            "div_DVA_prev_NDA_threshold_buy",
            "deltadelta_threshold_buy",
            "nda_threshold_buy",
            "stop_loss",
            "take_profit",
            "leverage",
            "trade_direction",
        ]
        labels = {
            "div_DVA_prev_NDA_threshold_buy": "Beschleunigung (DVA / prev NDA) – Kaufschwelle",
            "deltadelta_threshold_buy": "DeltaDelta (geglättetes Momentum) – Kaufschwelle",
            "nda_threshold_buy": "NDA (normalisierte Preisänderung) – Kaufschwelle",
            "stop_loss": "Stop Loss (%)",
            "take_profit": "Take Profit (%)",
            "leverage": "Hebel (nur Futures)",
            "trade_direction": "Handelsrichtung",
        }
        help_texts = {
            "div_DVA_prev_NDA_threshold_buy": "Kaufschwelle für die Momentum-Beschleunigung (DVA / vorherige NDA). Entspricht „Beschleunigung“ im Backtesting.",
            "deltadelta_threshold_buy": "Kaufschwelle für das geglättete Zwei-Punkt-Momentum ((NDA + vorherige NDA) / 2). Entspricht „DeltaDelta“ im Backtesting.",
            "nda_threshold_buy": "Kaufschwelle für die normalisierte Preisänderung (((P0 - P1) / P1) * 100). Entspricht „NDA“ im Backtesting.",
            "stop_loss": "Prozentualer Verlust ab Einstieg zum automatischen Schließen der Position.",
            "take_profit": "Prozentualer Gewinn ab Einstieg zum automatischen Schließen der Position.",
            "leverage": "Hebel für Futures (1 = ohne Hebel). Gilt erst für neu eröffnete Positionen.",
            "trade_direction": "Long, Short oder beide Richtungen. Short setzt den Futures-Markt voraus.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Wie im Konfigurationsformular optional, damit bestehende
        # Dashboard-Formulare ohne die neuen Felder weiter funktionieren.
        self.fields["leverage"].required = False
        self.fields["trade_direction"].required = False
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs["class"] = "form-control"
            if field.help_text:
                field.widget.attrs["title"] = field.help_text

    def clean_leverage(self):
        return self.cleaned_data.get("leverage") or 1

    def clean_trade_direction(self):
        return self.cleaned_data.get("trade_direction") or LONG

    def clean(self):
        # Börse und Markt sind im Dashboard nicht editierbar; geprüft wird
        # deshalb gegen die gespeicherte Konfiguration.
        cleaned_data = super().clean()
        instance = self.instance
        validate_leverage_and_direction(
            self,
            cleaned_data,
            getattr(instance, "exchange", None),
            getattr(instance, "market", None),
        )
        return cleaned_data


class BacktestForm(forms.Form):
    template = forms.ChoiceField(
        label="Backtesting-Template",
        choices=template_choices(),
        required=False,
        help_text="Ein Template setzt nur sinnvolle Startwerte für das Suchraster; die Live-Konfiguration bleibt unverändert.",
    )
    acc_from = forms.FloatField(
        label="Beschleunigung (DVA / prev NDA) – von",
        help_text="Startwert des Suchbereichs für die relative Momentum-Beschleunigung (DVA / vorherige NDA). Entspricht „div_DVA_prev_NDA_threshold_buy“ in der Konfiguration.",
    )
    acc_to = forms.FloatField(
        label="Beschleunigung (DVA / prev NDA) – bis",
        help_text="Endwert des Suchbereichs für die Beschleunigung (DVA / vorherige NDA).",
    )
    acc_steps = forms.FloatField(
        label="Beschleunigung (DVA / prev NDA) – Schrittweite",
        min_value=1e-12,
        help_text="Schrittweite für das Suchraster der Beschleunigung (z. B. 0.1).",
    )
    nda_from = forms.FloatField(
        label="NDA (normalisierte Preisänderung) – von",
        help_text="Startwert des Suchbereichs für die normalisierte Preisänderung in % (((P0 - P1) / P1) * 100). Entspricht „nda_threshold_buy“ in der Konfiguration.",
    )
    nda_to = forms.FloatField(
        label="NDA (normalisierte Preisänderung) – bis",
        help_text="Endwert des Suchbereichs für NDA.",
    )
    nda_steps = forms.FloatField(
        label="NDA (normalisierte Preisänderung) – Schrittweite",
        min_value=1e-12,
        help_text="Schrittweite für das Suchraster von NDA (z. B. 0.05).",
    )
    deltadelta_from = forms.FloatField(
        label="DeltaDelta (geglättetes Momentum) – von",
        help_text="Startwert des Suchbereichs für das geglättete Zwei-Punkt-Momentum ((NDA + vorherige NDA) / 2). Entspricht „deltadelta_threshold_buy“ in der Konfiguration.",
    )
    deltadelta_to = forms.FloatField(
        label="DeltaDelta (geglättetes Momentum) – bis",
        help_text="Endwert des Suchbereichs für DeltaDelta.",
    )
    deltadelta_steps = forms.FloatField(
        label="DeltaDelta (geglättetes Momentum) – Schrittweite",
        min_value=1e-12,
        help_text="Schrittweite für das Suchraster von DeltaDelta (z. B. 0.05).",
    )
    trade_amount = forms.FloatField(
        label="Trade-Betrag pro Position (USDT)",
        min_value=0.00000001,
        help_text="Eingesetzte Margin je Position. Das Nominalvolumen beträgt Margin × Hebel.",
    )
    direction = forms.ChoiceField(
        label="Handelsrichtung",
        choices=DIRECTION_CHOICES,
        required=False,
        help_text="Simulierte Richtung: Long, Short oder beide. Short setzt Futures voraus.",
    )
    leverage = forms.IntegerField(
        label="Hebel (nur Futures)",
        required=False,
        min_value=1,
        help_text="Hebel für die Simulation. Im Spot-Markt wird immer mit 1 gerechnet.",
    )
    take_profit = forms.FloatField(
        label="Take Profit (%)",
        min_value=0.001,
        max_value=100,
        help_text="Prozentualer Kursgewinn ab Einstieg zum Schließen im Backtest.",
    )
    stop_loss = forms.FloatField(
        label="Stop Loss (%)",
        min_value=0.001,
        max_value=100,
        help_text="Prozentualer Kursverlust ab Einstieg zum Schließen im Backtest.",
    )
    fee = forms.FloatField(
        label="Handelsgebühr je Order (%)",
        min_value=0,
        max_value=5,
        help_text="Simulierte Handelsgebühr in Prozent, die je Kauf und Verkauf abgezogen wird.",
    )
    max_price_points = forms.IntegerField(
        label="Maximale historische Preispunkte",
        min_value=100,
        max_value=_MAX_BACKTEST_PRICE_POINTS,
        initial=_MAX_BACKTEST_PRICE_POINTS,
        help_text="Anzahl der jüngsten Datenpunkte je Symbol aus DataLog. Das Hardwareprofil begrenzt den Wert zusätzlich.",
    )
    max_grid_points = forms.IntegerField(
        label="Maximale Rasterpunkte je Parameter",
        required=False,
        min_value=2,
        max_value=100,
        initial=25,
        help_text="Begrenzt die Anzahl der Werte je Indikatorachse zusätzlich zum Gesamtkombinationslimit.",
    )
    max_combinations = forms.IntegerField(
        label="Maximale Kombinationen (Hard-Limit)",
        required=False,
        min_value=100,
        max_value=_MAX_BACKTEST_COMBINATIONS,
        initial=_MAX_BACKTEST_COMBINATIONS,
        help_text="Harte Obergrenze über alle Symbole. Sie kann per BACKTEST_MAX_COMBINATIONS und Hardwareprofil angepasst, aber nie überschritten werden.",
    )
    schedule_backtest = forms.BooleanField(
        label="Backtest für späteren Zeitpunkt planen?",
        required=False,
        help_text="Aktivieren, um den Backtest zu einer geplanten Zeit statt sofort auszuführen.",
    )
    scheduled_start_time = forms.DateTimeField(
        label="Geplante Startzeit",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text="Datum und Uhrzeit in der Zukunft für die geplante Ausführung.",
    )

    def __init__(
        self,
        *args,
        start_capital=None,
        resource_profile=None,
        symbol_count=1,
        exchange=None,
        market=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.start_capital = start_capital
        self.exchange = exchange
        self.market = market
        if market is not None and market != "futures":
            # Spot: Hebel und Short sind fachlich ausgeschlossen, deshalb wird
            # das Feld gar nicht erst zur Eingabe angeboten.
            self.fields["leverage"].disabled = True
            self.fields["leverage"].initial = 1
            self.fields["direction"].choices = [
                choice for choice in DIRECTION_CHOICES if choice[0] == LONG
            ]
        elif exchange:
            limit = max_leverage(exchange)
            self.fields["leverage"].max_value = limit
            self.fields["leverage"].help_text = (
                f"Hebel für die Simulation (1 bis {limit}x auf dieser Börse). "
                "Der Trade-Betrag ist die eingesetzte Margin."
            )
        self.resource_profile = resource_profile
        self.symbol_count = max(1, int(symbol_count or 1))
        profile_max_points = getattr(
            resource_profile, "max_price_points", _MAX_BACKTEST_PRICE_POINTS
        )
        profile_max_grid = getattr(resource_profile, "max_grid_points", 25)
        profile_max_combinations = getattr(
            resource_profile, "max_combinations", _MAX_BACKTEST_COMBINATIONS
        )
        self.max_price_points_limit = max(
            100, min(_MAX_BACKTEST_PRICE_POINTS, int(profile_max_points))
        )
        self.max_combinations_limit = max(
            100, min(_MAX_BACKTEST_COMBINATIONS, int(profile_max_combinations))
        )
        self.max_grid_points_limit = max(2, min(100, int(profile_max_grid)))
        self.fields["max_price_points"].max_value = self.max_price_points_limit
        self.fields["max_price_points"].initial = self.max_price_points_limit
        self.fields["max_grid_points"].max_value = self.max_grid_points_limit
        self.fields["max_grid_points"].initial = self.max_grid_points_limit
        self.fields["max_combinations"].max_value = self.max_combinations_limit
        self.fields["max_combinations"].initial = self.max_combinations_limit
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"
            if field.help_text:
                field.widget.attrs["title"] = field.help_text

    def _clean_leverage_and_direction(self, cleaned_data):
        """Normalisiert Hebel und Richtung und prüft sie gegen die Börse."""
        market = (self.market or "").strip().lower()
        direction = cleaned_data.get("direction") or LONG
        leverage = cleaned_data.get("leverage") or 1
        if market and market != "futures":
            if direction in {SHORT, BOTH}:
                self.add_error("direction", "Short ist nur im Futures-Markt simulierbar.")
            cleaned_data["direction"] = LONG
            cleaned_data["leverage"] = 1
            return
        if self.exchange:
            limit = max_leverage(self.exchange)
            if leverage > limit:
                self.add_error(
                    "leverage",
                    f"Diese Börse erlaubt höchstens Hebel {limit}x.",
                )
                return
        effective = int(resolve_leverage(self.exchange or "", market or "futures", leverage))
        stop_loss = cleaned_data.get("stop_loss")
        if stop_loss is not None:
            liquidation = liquidation_move_percent(effective)
            if Decimal(str(stop_loss)) >= liquidation:
                self.add_error(
                    "stop_loss",
                    f"Bei Hebel {effective}x liegt die Liquidation bereits bei "
                    f"{liquidation:.2f} % Kursverlust.",
                )
        cleaned_data["direction"] = direction
        cleaned_data["leverage"] = effective

    def clean(self):
        cleaned_data = super().clean()
        self._clean_leverage_and_direction(cleaned_data)
        scheduled = cleaned_data.get("schedule_backtest")
        start_time = cleaned_data.get("scheduled_start_time")
        if scheduled and not start_time:
            self.add_error("scheduled_start_time", "Bitte eine Startzeit angeben.")
        elif start_time and start_time <= timezone.now():
            self.add_error("scheduled_start_time", "Die Startzeit muss in der Zukunft liegen.")

        trade_amount = cleaned_data.get("trade_amount")
        fee = cleaned_data.get("fee") or 0
        # Gebunden wird die Margin; die Gebühr fällt auf Margin × Hebel an.
        leverage = float(cleaned_data.get("leverage") or 1)
        if (
            self.start_capital is not None
            and trade_amount is not None
            and trade_amount * (1 + leverage * fee / 100) > float(self.start_capital)
        ):
            self.add_error(
                "trade_amount",
                "Trade-Betrag inklusive Kaufgebühr übersteigt das Startkapital.",
            )

        dimensions = []
        for prefix in ("acc", "nda", "deltadelta"):
            start = cleaned_data.get(f"{prefix}_from")
            end = cleaned_data.get(f"{prefix}_to")
            step = cleaned_data.get(f"{prefix}_steps")
            if start is None or end is None or step is None:
                continue
            if not all(math.isfinite(value) for value in (start, end, step)):
                self.add_error(f"{prefix}_from", "Nur endliche Zahlen sind erlaubt.")
                continue
            if start > end:
                self.add_error(f"{prefix}_to", "„Bis“ muss größer oder gleich „Von“ sein.")
                continue
            dimensions.append(math.floor((end - start) / step + 1e-9) + 1)

        max_grid_points = cleaned_data.get("max_grid_points") or self.max_grid_points_limit
        if max_grid_points > self.max_grid_points_limit:
            self.add_error(
                "max_grid_points",
                f"Das aktuelle Hardwareprofil erlaubt höchstens {self.max_grid_points_limit} Rasterpunkte je Parameter.",
            )
        cleaned_data["max_grid_points"] = max_grid_points
        max_price_points = cleaned_data.get("max_price_points")
        if max_price_points is not None and max_price_points > self.max_price_points_limit:
            self.add_error(
                "max_price_points",
                f"Das aktuelle Hardwareprofil erlaubt höchstens {self.max_price_points_limit:,} Preispunkte.",
            )
        max_combinations = cleaned_data.get("max_combinations") or self.max_combinations_limit
        if max_combinations > self.max_combinations_limit:
            self.add_error(
                "max_combinations",
                f"Das aktuelle Hardwareprofil erlaubt höchstens {self.max_combinations_limit:,} Kombinationen.",
            )
        cleaned_data["max_combinations"] = max_combinations
        if len(dimensions) == 3 and any(dimension > max_grid_points for dimension in dimensions):
            self.add_error(
                None,
                f"Jede Rasterachse darf höchstens {max_grid_points} Werte enthalten.",
            )
        if len(dimensions) == 3:
            combinations_per_symbol = math.prod(dimensions)
            total_combinations = combinations_per_symbol * self.symbol_count
            if total_combinations > max_combinations:
                raise forms.ValidationError(
                    f"Zu viele Kombinationen ({total_combinations:,} über {self.symbol_count} Symbol(e)). "
                    f"Das aktuelle Hard-Limit beträgt {max_combinations:,}."
                )
        return cleaned_data
