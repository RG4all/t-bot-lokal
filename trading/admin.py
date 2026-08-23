from django.contrib import admin

from .models import BacktestTask, Configuration, DataLog, ErrorLog, TradingLog


@admin.register(Configuration)
class ConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "user",
        "exchange",
        "market",
        "leverage",
        "trade_direction",
        "symbols",
        "start_capital",
        "trade_amount",
        "take_profit",
        "stop_loss",
        "fee",
        "is_running",
        "countdown",
        "time_interval",
    )

    list_filter = (
        "exchange",
        "market",
        "trade_direction",
        "user",
    )

    search_fields = (
        "name",
        "symbols",
        "user__username",
    )

    readonly_fields = ()

    fieldsets = (
        (
            "Grunddaten",
            {
                "fields": (
                    "name",
                    "user",
                    "exchange",
                    "market",
                    "leverage",
                    "trade_direction",
                    "symbols",
                )
            },
        ),
        (
            "Kapital & Risiko",
            {"fields": ("start_capital", "trade_amount", "take_profit", "stop_loss", "fee")},
        ),
        (
            "Strategie",
            {
                "fields": (
                    "countdown",
                    "time_interval",
                    "sales_stop_threshold",
                    "countdown_reset_indicators",
                    "div_DVA_prev_NDA_threshold_buy",
                    "deltadelta_threshold_buy",
                    "nda_threshold_buy",
                )
            },
        ),
        ("API", {"fields": ("api_key", "secret_key")}),
    )


@admin.register(TradingLog)
class TradingLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "configuration",
        "symbol",
        "action",
        "direction",
        "leverage",
        "price",
        "amount",
        "pl_nominal",
        "pl_relative",
        "total_pl",
    )

    list_filter = ("symbol", "action", "direction")
    search_fields = ("symbol",)
    ordering = ("-timestamp",)


@admin.register(DataLog)
class DataLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "configuration",
        "symbol",
        "price",
        "nda",
        "dva",
        "deltadelta",
    )

    list_filter = ("symbol",)
    ordering = ("-timestamp",)


@admin.register(BacktestTask)
class BacktestTaskAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "configuration",
        "symbol",
        "status",
        "progress",
        "created_at",
        "completed_at",
        "is_scheduled",
    )

    list_filter = (
        "status",
        "is_scheduled",
    )

    readonly_fields = (
        "progress",
        "created_at",
        "completed_at",
    )


@admin.register(ErrorLog)
class ErrorLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "configuration",
        "severity",
        "source",
        "exception_type",
        "resolved",
        "message",
    )
    list_filter = ("severity", "resolved", "source")
    search_fields = (
        "message",
        "exception_type",
        "configuration__name",
        "configuration__user__username",
    )
    ordering = ("-timestamp",)
    readonly_fields = ("timestamp", "details")
