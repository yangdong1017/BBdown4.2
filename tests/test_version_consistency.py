from __future__ import annotations

import unittest
from pathlib import Path

from core.config import APP_VERSION, WINDOW_TITLE


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class VersionConsistencyTests(unittest.TestCase):
    def test_release_metadata_uses_app_version(self) -> None:
        installer = (PROJECT_ROOT / "installer.iss").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8-sig")
        development_guide = (PROJECT_ROOT / "#开发文档，AI禁止删除#.md").read_text(encoding="utf-8-sig")

        self.assertEqual(APP_VERSION, "4.2")
        self.assertEqual(WINDOW_TITLE, "BBDown 4.2")
        self.assertIn('#define MyAppVersion "4.2"', installer)
        self.assertIn("# BBDown4.2", readme)
        self.assertIn("BBDown-4.2.exe", readme)
        self.assertIn("BBDown-4.2.zip", readme)
        self.assertIn("release_assets\\v4.2", development_guide)

    def test_no_stale_previous_version_in_release_instructions(self) -> None:
        """A half-renamed release would upload the wrong file name."""
        installer = (PROJECT_ROOT / "installer.iss").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8-sig")

        for previous in ("4.0", "4.1"):
            self.assertNotIn(f"BBDown-{previous}", readme)
            self.assertNotIn(f"release_assets\\v{previous}", readme)
            self.assertNotIn(f'MyAppVersion "{previous}"', installer)


if __name__ == "__main__":
    unittest.main()
