"""Kuerzt Trade-Historie und Fehlerlog auf konfigurierbare Obergrenzen (O5).

DataLogs trimmt der Bot laufend je Symbol; TradingLogs und ErrorLogs wachsen
ohne Pflege unbegrenzt. Der Command loescht in Batches – nie ein einzelner
Massen-DELETE, der die kleine Datenbank lange sperren wuerde. Er ist auf
Raumabbau ausgelegt und laeuft typischerweise als Cronjob oder manuell nach
Capacity-Engpaessen.

Beispiele:
    python manage.py prune_history --dry-run
    python manage.py prune_history --trading-logs-per-config 100000
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from trading.models import Configuration, ErrorLog, TradingLog

# Groesse einer Loesch-Charge; haelt jede Transaktion kurz (vgl.
# db_trim_datalog, das nach demselben Muster trimmt).
_BATCH_SIZE = 1_000


class Command(BaseCommand):
    help = (
        "Begrenzt TradingLogs je Konfiguration und ErrorLogs global; loescht "
        "zusaetzlich geloeste oder Info-Fehlermeldungen nach Ablauf der "
        "Aufbewahrungsfrist."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--trading-logs-per-config",
            type=int,
            default=settings.MAX_TRADING_LOGS_PER_CONFIG,
            help=(
                f"Anzahl zu behaltender TradingLogs je Konfiguration "
                f"(Default: {settings.MAX_TRADING_LOGS_PER_CONFIG})."
            ),
        )
        parser.add_argument(
            "--error-logs",
            type=int,
            default=settings.MAX_ERROR_LOGS,
            help=f"Anzahl zu behaltender ErrorLogs gesamt (Default: {settings.MAX_ERROR_LOGS}).",
        )
        parser.add_argument(
            "--error-days",
            type=int,
            default=settings.ERROR_LOG_RETENTION_DAYS,
            help=(
                f"Aufbewahrungstage fuer geloeste oder Info-Fehlermeldungen "
                f"(Default: {settings.ERROR_LOG_RETENTION_DAYS})."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Zeigt nur, was geloescht wuerde, ohne zu loeschen.",
        )

    def handle(self, *args, **options):
        cap_trading = options["trading_logs_per_config"]
        cap_errors = options["error_logs"]
        retention_days = options["error_days"]
        dry_run = options["dry_run"]
        if cap_trading < 1 or cap_errors < 1 or retention_days < 1:
            raise CommandError("Alle Grenzen muessen >= 1 sein.")

        total = 0
        for config in Configuration.objects.iterator():
            cutoff_id = self._cutoff_id(
                TradingLog.objects.filter(configuration_id=config.id), cap_trading
            )
            if cutoff_id is None:
                continue
            deleted = self._delete_below(
                TradingLog.objects.filter(configuration_id=config.id), cutoff_id, dry_run
            )
            total += deleted
            action = "zu loeschen (dry-run)" if dry_run else "geloescht"
            self.stdout.write(
                f"Konfiguration {config.id}: {deleted} TradingLogs {action}, "
                f"Cap {cap_trading} je Konfiguration."
            )

        cutoff_id = self._cutoff_id(ErrorLog.objects.all(), cap_errors)
        if cutoff_id is not None:
            deleted = self._delete_below(ErrorLog.objects.all(), cutoff_id, dry_run)
            total += deleted
            action = "zu loeschen (dry-run)" if dry_run else "geloescht"
            self.stdout.write(
                f"Fehlerlog: {deleted} Eintraege {action}, Cap {cap_errors} gesamt."
            )

        stale = ErrorLog.objects.filter(
            timestamp__lt=timezone.now() - timedelta(days=retention_days)
        ).filter(Q(resolved=True) | Q(severity="info"))
        if dry_run:
            stale_count = stale.count()
            total += stale_count
            self.stdout.write(
                f"Fehlerlog: {stale_count} geloeste/Info-Eintraege aelter als "
                f"{retention_days} Tage (dry-run)."
            )
        else:
            deleted = self._delete_queryset_batched(stale)
            if deleted:
                total += deleted
                self.stdout.write(
                    f"Fehlerlog: {deleted} geloeste/Info-Eintraege aelter als "
                    f"{retention_days} Tage geloescht."
                )

        verb = "geprueft (dry-run)" if dry_run else "bereinigt"
        self.stdout.write(self.style.SUCCESS(f"History {verb}: {total} Eintraege."))

    @staticmethod
    def _cutoff_id(queryset, keep):
        """ID des aeltesten zu loeschenden Eintrags; alles darunter auch.

        Die (keep+1)-te-neueste Zeile ist die erste, die dem Cap weicht; ueber
        die numerische ID (nicht den Zeitstempel) zu loeschen haelt die
        Auswahl stabil, auch wenn waehrend der Pflege neue Zeilen entstehen.

        Returns:
            ``None``, wenn die queryset nicht ueber das Limit hinauswaechst.
        """
        return queryset.order_by("-id").values_list("id", flat=True)[keep : keep + 1].first()

    @staticmethod
    def _delete_below(queryset, cutoff_id, dry_run):
        """Loescht Zeilen mit ``id <= cutoff_id`` in Batches (oder zaehlt sie)."""
        if dry_run:
            return queryset.filter(id__lte=cutoff_id).count()
        return Command._delete_queryset_batched(queryset.filter(id__lte=cutoff_id))

    @staticmethod
    def _delete_queryset_batched(queryset):
        deleted = 0
        while True:
            with transaction.atomic():
                batch_ids = list(queryset.order_by("id").values_list("id", flat=True)[:_BATCH_SIZE])
                if not batch_ids:
                    return deleted
                queryset.filter(id__in=batch_ids).delete()
                deleted += len(batch_ids)
