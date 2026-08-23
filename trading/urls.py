from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health_view, name="health"),
    path("help/", views.help_view, name="help"),
    path("gate/", views.passphrase_gate_view, name="passphrase_gate"),
    path("", views.home, name="home"),
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("config/", views.config_view, name="config"),
    path("config/list/", views.config_list_view, name="config_list"),
    path("config/edit/<int:config_id>/", views.config_edit_view, name="config_edit"),
    path("config/delete/<int:config_id>/", views.config_delete, name="config_delete"),
    path(
        "config/activate/<int:config_id>/",
        views.config_activate,
        name="config_activate",
    ),
    path(
        "config/deactivate/<int:config_id>/",
        views.config_deactivate,
        name="config_deactivate",
    ),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("reset_log/<int:config_id>/", views.reset_log, name="reset_log"),
    path("api/info/<int:config_id>/", views.info_api, name="info_api"),
    path("api/logs/<int:config_id>/", views.logs_api, name="logs_api"),
    path("api/manual_sell/<int:config_id>/", views.manual_sell_view, name="manual_sell"),
    path("api/kill_switch/<int:config_id>/", views.kill_switch_view, name="kill_switch"),
    path("api/symbols/", views.symbol_suggestions_api, name="symbol_suggestions_api"),
    path(
        "api/market-opportunities/",
        views.market_opportunities_api,
        name="market_opportunities_api",
    ),
    # Lesbarer Alias für bestehende Frontend-Integrationen.
    path("api/top-movers/", views.market_opportunities_api, name="top_movers_api"),
    path("api/resources/", views.server_resources_api, name="server_resources_api"),
    path(
        "api/backtesting/estimate/",
        views.backtesting_estimate_api,
        name="backtesting_estimate_api",
    ),
    path("api/data_logs/", views.data_logs_api, name="data_logs_api"),
    path("api/trades/", views.trades_api, name="trades_api"),
    path("api/bot/status/", views.bot_status_api, name="bot_status_api"),
    path("report/<int:config_id>/", views.generate_report, name="generate_report"),
    path(
        "report/<int:config_id>/html/",
        views.generate_report_html,
        name="generate_report_html",
    ),
    path(
        "report/<int:config_id>/csv/",
        views.generate_report_csv,
        name="generate_report_csv",
    ),
    path("backtesting/", views.backtesting_index, name="backtesting_index"),
    path(
        "api/backtesting/status/",
        views.backtesting_status_api,
        name="backtesting_status_api",
    ),
    path(
        "backtesting/<int:config_id>/",
        views.backtesting_form,
        name="backtesting_form",
    ),
    path(
        "backtesting/control/<int:task_id>/",
        views.control_backtest,
        name="control_backtest",
    ),
    path(
        "backtesting/<int:task_id>/pdf/",
        views.generate_backtest_pdf,
        name="generate_backtest_pdf",
    ),
    path("analyse/", views.analyse_view, name="analyse"),
    path("errors/", views.error_log_view, name="error_log"),
    path(
        "errors/<int:error_id>/resolve/",
        views.error_log_resolve,
        name="error_log_resolve",
    ),
]
