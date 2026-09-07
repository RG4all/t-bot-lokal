import os
import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from scripts.local_hardware import HardwareInfo, recommend_profile
from scripts.tune_local_hardware import build_environment
from trading_bot_project.celery_config import CELERY_RUNTIME_CONFIG

ROOT = Path(__file__).resolve().parents[2]


class HardwareTuningTests(SimpleTestCase):
    def test_native_profile_uses_local_hardware_not_render_simulation(self):
        hardware = HardwareInfo("x86_64", 8, 16_384, 100_000, 500_000, 400)
        profile = recommend_profile(hardware)
        self.assertFalse(profile.simulate_render_free)
        self.assertGreater(profile.web_cpus, 0.1)
        self.assertEqual(profile.worker_memory, "1024m")
        self.assertEqual(profile.env()["CELERY_WORKER_MAX_MEMORY_PER_CHILD"], "384000")
        self.assertEqual(profile.env()["BACKTEST_DEFAULT_PRICE_POINTS"], "5000")
        self.assertEqual(profile.env()["DATA_LOG_WRITE_INTERVAL_SECONDS"], "5")

    def test_render_simulation_requires_explicit_flag(self):
        hardware = HardwareInfo("aarch64", 4, 8_192, 50_000, 300_000, 200)
        normal = recommend_profile(hardware)
        simulated = recommend_profile(hardware, simulate_render_free=True)
        self.assertGreater(normal.web_cpus, 0.1)
        self.assertEqual(simulated.web_cpus, 0.1)
        self.assertTrue(simulated.simulate_render_free)

    def test_generated_environment_preserves_existing_secrets(self):
        hardware = HardwareInfo("x86_64", 4, 8_192, 50_000, 300_000, 200)
        values = build_environment(
            recommend_profile(hardware),
            {"SECRET_KEY": "keep-me", "PASSPHRASE": "keep-gate", "POSTGRES_PASSWORD": "keep-db"},
        )
        self.assertEqual(values["SECRET_KEY"], "keep-me")
        self.assertEqual(values["PASSPHRASE"], "keep-gate")
        self.assertEqual(values["POSTGRES_PASSWORD"], "keep-db")
        self.assertEqual(values["SIMULATE_RENDER_FREE"], "False")

    def test_tuner_generates_independent_url_safe_passphrases(self):
        hardware = HardwareInfo("x86_64", 4, 8_192, 50_000, 300_000, 200)
        profile = recommend_profile(hardware)
        first = build_environment(profile, {})["PASSPHRASE"]
        second = build_environment(profile, {})["PASSPHRASE"]
        self.assertRegex(first, r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(first, second)

    def test_tuner_writes_private_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / ".env.local"
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts/tune_local_hardware.py"),
                    "--no-benchmark",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            text = output.read_text()
            self.assertIn("SIMULATE_RENDER_FREE=False", text)
            self.assertIn("REDIS_MAXMEMORY=", text)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


class InstallerAndCeleryPolicyTests(SimpleTestCase):
    def test_installer_detects_major_package_families_in_dry_run(self):
        script = ROOT / "scripts/install_system_dependencies.sh"
        cases = (
            ("apt", "debian"),
            ("pacman", "arch"),
            ("dnf", "fedora"),
            ("dnf", "rocky"),
            ("zypper", "opensuse-tumbleweed"),
            ("apk", "alpine"),
        )
        for family, distro in cases:
            with self.subTest(family=family, distro=distro):
                env = {
                    **os.environ,
                    "TBOT_PACKAGE_FAMILY": family,
                    "TBOT_DISTRO_ID": distro,
                    "TBOT_ARCH": "x86_64",
                }
                result = subprocess.run(
                    ["bash", str(script), "--dry-run"],
                    cwd=ROOT,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertIn(f"family={family}", result.stdout)
                self.assertIn("architecture=x86_64", result.stdout)

    def test_setup_dry_run_keeps_render_simulation_disabled_by_default(self):
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/setup_local.sh"), "--dry-run"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("Render-Free simulation=0", result.stdout)
        self.assertIn("docker compose", result.stdout)

    def test_celery_policy_matches_hard_limits(self):
        self.assertEqual(CELERY_RUNTIME_CONFIG["worker_concurrency"], 1)
        self.assertEqual(CELERY_RUNTIME_CONFIG["worker_prefetch_multiplier"], 1)
        self.assertEqual(CELERY_RUNTIME_CONFIG["worker_max_tasks_per_child"], 1)
        self.assertEqual(CELERY_RUNTIME_CONFIG["worker_max_memory_per_child"], 384_000)
        self.assertEqual(CELERY_RUNTIME_CONFIG["task_soft_time_limit"], 3_600)
        self.assertEqual(CELERY_RUNTIME_CONFIG["task_time_limit"], 3_900)
        self.assertTrue(CELERY_RUNTIME_CONFIG["task_acks_late"])
        self.assertTrue(CELERY_RUNTIME_CONFIG["task_reject_on_worker_lost"])
