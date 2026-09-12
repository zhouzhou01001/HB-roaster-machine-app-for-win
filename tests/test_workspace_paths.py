import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utils.paths import (
    APP_NAME,
    clear_workspace_issues,
    ensure_workspace_dirs,
    get_workspace_issues,
)


class WorkspacePathTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_workspace_issues()

    def test_localappdata_unwritable_falls_back_to_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            fake_appdata = Path(temp_root) / "appdata"
            fake_localappdata = Path(temp_root) / "localappdata"

            appdata_root = fake_appdata / APP_NAME
            local_root = fake_localappdata / "RoastMonitor" / APP_NAME
            appdata_root.parent.mkdir(parents=True, exist_ok=True)
            local_root.parent.mkdir(parents=True, exist_ok=True)
            appdata_root.write_text("appdata_conflict", encoding="utf-8")
            local_root.write_text("local_conflict", encoding="utf-8")

            with patch.dict(os.environ, {"APPDATA": str(fake_appdata), "LOCALAPPDATA": str(fake_localappdata)}):
                with patch("app.utils.paths.tempfile.gettempdir", return_value=str(Path(temp_root))):
                    data_dir, config_dir = ensure_workspace_dirs()

            expected_temp_root = Path(temp_root) / "RoastMonitor" / APP_NAME
            self.assertEqual(data_dir.parent, expected_temp_root)
            self.assertEqual(config_dir.parent, expected_temp_root)
            self.assertTrue(data_dir.is_dir())
            self.assertTrue(config_dir.is_dir())

            issues = get_workspace_issues()
            issues_text = "\n".join(issues)
            self.assertIn(str(appdata_root), issues_text)
            self.assertIn(str(local_root), issues_text)
            self.assertEqual(len(issues), 2)
            self.assertIn("工作区数据目录", issues_text)
