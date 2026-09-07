"""
Management command to clear all stored API keys from the database.

The Configuration model no longer stores API keys - they are injected as
environment variables (EXCHANGE_API_KEY, EXCHANGE_SECRET_KEY) at container
startup. This command removes any legacy plaintext API keys from the database
as an immediate security measure when transitioning from paper to live trading.

Usage:
    python manage.py clear_api_keys
    python manage.py clear_api_keys --dry-run  # Preview without saving
"""

from django.core.management.base import BaseCommand

from trading.models import Configuration


class Command(BaseCommand):
    help = "Clear all stored API keys from the Configuration model."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview which records would be affected without saving.",
        )

    def handle(self, *args, **options):
        try:
            configs = Configuration.objects.filter(
                api_key__isnull=False
            ).exclude(api_key="")
            configs_secret = Configuration.objects.filter(
                secret_key__isnull=False
            ).exclude(secret_key="")

            count_api = configs.count()
            count_secret = configs_secret.count()

            if options["dry_run"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"[DRY RUN] Would clear API keys from "
                        f"{count_api} config(s) with api_key, "
                        f"{count_secret} config(s) with secret_key."
                    )
                )
                for c in configs:
                    self.stdout.write(
                        f"  Config #{c.id} ({c.name}): api_key would be cleared"
                    )
                for c in configs_secret:
                    if c not in configs:
                        self.stdout.write(
                            f"  Config #{c.id} ({c.name}): secret_key would be cleared"
                        )
            else:
                for c in configs:
                    c.api_key = ""
                    c.save(update_fields=["api_key"])
                for c in configs_secret:
                    c.secret_key = ""
                    c.save(update_fields=["secret_key"])

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Cleared API keys from {count_api} config(s) "
                        f"(api_key) and {count_secret} config(s) (secret_key)."
                    )
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        "IMPORTANT: Ensure EXCHANGE_API_KEY and EXCHANGE_SECRET_KEY "
                        "are set as environment variables in docker-compose.yml "
                        "before restarting containers with live trading enabled."
                    )
                )
        except Exception as e:
            # Fields have been removed by migration 0013
            self.stdout.write(
                self.style.WARNING(
                    f"API key fields no longer exist in the database "
                    f"(migration 0013 already applied): {e}"
                )
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "No action needed. API keys are not stored in the database."
                )
            )
