from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.gui.services import automation, jobs


class AutomationNotificationTests(unittest.TestCase):
    def test_sanitize_settings_adds_notification_and_send_mail_action(self) -> None:
        settings = automation.sanitize_settings({"actions": {"collect_docs": {"dailyEnabled": True}}})

        self.assertEqual(settings["notificationEmailRecipient"], "")
        self.assertIn("send_meeting_mail", settings["actions"])
        self.assertEqual(settings["actions"]["send_meeting_mail"]["dailyHour"], 9)
        self.assertTrue(settings["actions"]["collect_docs"]["dailyEnabled"])

    def test_start_job_records_automation_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            jobs_dir = Path(tmpdir) / "jobs"
            token = jobs.AUTOMATION_CONTEXT.set({"action": "collect_docs", "schedule": "daily", "key": "daily:key"})
            try:
                with patch.object(jobs, "JOBS_DIR", jobs_dir), patch.object(
                    jobs,
                    "ensure_gui_dirs",
                    lambda: jobs_dir.mkdir(parents=True, exist_ok=True),
                ), patch("scripts.gui.services.jobs.subprocess.Popen"):
                    job = jobs.start_job("collect_docs", ["python", "--version"])
            finally:
                jobs.AUTOMATION_CONTEXT.reset(token)

            status = json.loads((job.dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["automation"]["action"], "collect_docs")
            self.assertEqual(status["automation"]["schedule"], "daily")
            self.assertEqual(status["automation"]["key"], "daily:key")


if __name__ == "__main__":
    unittest.main()
