from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from core.models import DouyinVideoConfig
from ui.douyin_video_page import (
    DOUYIN_STANDARD_AUDIO_LINK,
    DOUYIN_STANDARD_VIDEO_LINK,
    DouyinVideoPage,
)


class DouyinVideoPageUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _create_page(self) -> tuple[DouyinVideoPage, object]:
        page_config = DouyinVideoConfig(
            urls="",
            save_dir=tempfile.gettempdir(),
            concurrency=5,
        )
        load_patch = patch("ui.douyin_video_page.load_douyin_video_config", return_value=page_config)
        save_patch = patch("ui.douyin_video_page.save_douyin_video_config")
        load_patch.start()
        save_patch.start()
        self.addCleanup(load_patch.stop)
        self.addCleanup(save_patch.stop)
        return DouyinVideoPage(), page_config

    def test_directory_and_concurrency_controls_are_in_right_settings_card(self) -> None:
        page, page_config = self._create_page()

        self.assertTrue(page.download_settings_card.isAncestorOf(page.choose_dir_btn))
        self.assertTrue(page.download_settings_card.isAncestorOf(page.open_dir_btn))
        self.assertTrue(page.download_settings_card.isAncestorOf(page.dir_label))
        self.assertTrue(page.download_settings_card.isAncestorOf(page.concurrency_combo))
        self.assertFalse(page.input_card.isAncestorOf(page.choose_dir_btn))
        self.assertEqual(page.choose_dir_btn.text(), "选择保存目录")
        self.assertIn(page_config.save_dir, page.dir_label.text())

        page._set_running_state(True)
        self.assertFalse(page.choose_dir_btn.isEnabled())
        self.assertFalse(page.concurrency_combo.isEnabled())
        self.assertFalse(page.download_type_segment.isEnabled())
        page._set_running_state(False)
        self.assertTrue(page.choose_dir_btn.isEnabled())
        self.assertTrue(page.concurrency_combo.isEnabled())
        self.assertTrue(page.download_type_segment.isEnabled())
        self.assertTrue(page.download_type_segment.items["audio"].isEnabled())
        page.deleteLater()

    def test_video_mode_and_standard_link_actions(self) -> None:
        page, _page_config = self._create_page()

        self.assertEqual(page.download_type_segment.currentRouteKey(), "video")
        self.assertTrue(page.download_type_segment.items["video"].isEnabled())
        self.assertTrue(page.download_type_segment.items["audio"].isEnabled())
        self.assertEqual(page.url_edit.placeholderText(), "")
        self.assertIn(DOUYIN_STANDARD_VIDEO_LINK, page.standard_link_label.text())
        self.assertIn("标准视频链接", page.standard_link_label.text())
        self.assertTrue(page.input_card.isAncestorOf(page.standard_link_label))
        self.assertTrue(page.input_card.isAncestorOf(page.standard_link_actions))
        self.assertEqual(set(page.standard_link_actions.items), {"copy", "open"})
        self.assertEqual(page.input_card.layout().contentsMargins().top(), 9)
        self.assertEqual(page.input_card.layout().spacing(), 5)

        page.download_type_segment.items["audio"].click()
        self.assertEqual(page.download_type_segment.currentRouteKey(), "audio")
        self.assertIn(DOUYIN_STANDARD_AUDIO_LINK, page.standard_link_label.text())
        self.assertFalse(page.standard_link_actions.isHidden())
        self.assertEqual(page.start_btn.text(), "开始下载音频")
        self.assertTrue(page.start_btn.isEnabled())

        with patch("ui.douyin_video_page.QApplication.clipboard") as audio_clipboard:
            page.standard_link_actions.items["copy"].click()
            audio_clipboard.return_value.setText.assert_called_once_with(DOUYIN_STANDARD_AUDIO_LINK)

        page.download_type_segment.items["video"].click()
        self.assertEqual(page.download_type_segment.currentRouteKey(), "video")
        self.assertIn(DOUYIN_STANDARD_VIDEO_LINK, page.standard_link_label.text())
        self.assertFalse(page.standard_link_actions.isHidden())
        self.assertEqual(page.start_btn.text(), "开始下载视频")
        self.assertTrue(page.start_btn.isEnabled())

        with (
            patch("ui.douyin_video_page.QApplication.clipboard") as clipboard,
            patch("ui.douyin_video_page.QDesktopServices.openUrl", return_value=True) as open_url,
        ):
            page.standard_link_actions.items["copy"].click()
            clipboard.return_value.setText.assert_called_once_with(DOUYIN_STANDARD_VIDEO_LINK)
            self.assertEqual(page.standard_link_actions.currentRouteKey(), "copy")
            self.assertIn("已复制", page.status_label.text())

            page.standard_link_actions.items["open"].click()
            self.assertEqual(page.standard_link_actions.currentRouteKey(), "open")
            self.assertEqual(open_url.call_args.args[0].toString(), DOUYIN_STANDARD_VIDEO_LINK)

        page.deleteLater()

    def test_audio_mode_parses_audio_links_and_builds_audio_task_table(self) -> None:
        page, page_config = self._create_page()
        page.download_type_segment.items["audio"].click()
        page.url_edit.setPlainText(DOUYIN_STANDARD_AUDIO_LINK)

        links = page._current_links()
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].task_id, "7546439142222302011")
        self.assertIn("1 条可下载音频链接", page.count_label.text())

        page._populate_tasks(links)
        self.assertEqual(page.table.horizontalHeaderItem(0).text(), "文件名")
        self.assertEqual(page.table.item(0, 0).text(), "7546439142222302011.mp3")
        self.assertEqual(page.table.item(0, 1).text(), "0%")
        self.assertEqual(page.table.item(0, 2).text(), "等待中")

        page._save_config()
        self.assertEqual(page_config.audio_urls, DOUYIN_STANDARD_AUDIO_LINK)
        self.assertEqual(page_config.download_type, "audio")
        page.deleteLater()

    def test_right_settings_panel_uses_animated_collapse_and_expand(self) -> None:
        page, _page_config = self._create_page()
        page.resize(1020, 820)
        page.show()
        self.app.processEvents()

        self.assertTrue(page.right_panel.is_expanded)
        self.assertFalse(page.right_panel.content.isHidden())
        self.assertEqual(page.right_panel.animation.duration(), 220)

        page.right_panel.toggle_button.click()
        QTest.qWait(260)
        self.assertFalse(page.right_panel.is_expanded)
        self.assertTrue(page.right_panel.content.isHidden())

        page.right_panel.toggle_button.click()
        QTest.qWait(260)
        self.assertTrue(page.right_panel.is_expanded)
        self.assertFalse(page.right_panel.content.isHidden())
        self.assertGreater(page.right_panel.content.width(), 0)
        page.close()
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
