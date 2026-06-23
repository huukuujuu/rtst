from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rtst_app.app import (
    PENDING_TRANSLATION_TEXT,
    MainWindow,
    append_pending_history_entry,
    complete_history_entry,
)


_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class HistoryFlowTests(unittest.TestCase):
    def test_pending_source_is_completed_in_place(self) -> None:
        history: list[tuple[str, str]] = []

        self.assertTrue(append_pending_history_entry(history, "I'll take a rain check."))
        self.assertEqual(history, [("I'll take a rain check.", "")])

        self.assertTrue(
            complete_history_entry(
                history,
                "I'll take a rain check.",
                "다음으로 미룰게.",
            )
        )
        self.assertEqual(history, [("I'll take a rain check.", "다음으로 미룰게.")])

    def test_duplicate_pending_source_is_not_appended(self) -> None:
        history: list[tuple[str, str]] = [("No way.", "")]

        self.assertFalse(append_pending_history_entry(history, "No way."))
        self.assertEqual(history, [("No way.", "")])

    def test_repeated_pending_source_is_completed_everywhere(self) -> None:
        history: list[tuple[str, str]] = [("Same line.", ""), ("Other line.", ""), ("Same line.", "")]

        self.assertTrue(complete_history_entry(history, "Same line.", "같은 줄."))
        self.assertEqual(
            history,
            [("Same line.", "같은 줄."), ("Other line.", ""), ("Same line.", "같은 줄.")],
        )

    def test_completed_duplicate_is_not_appended(self) -> None:
        history: list[tuple[str, str]] = [("No way.", "말도 안 돼.")]

        self.assertFalse(complete_history_entry(history, "No way.", "말도 안 돼."))
        self.assertEqual(history, [("No way.", "말도 안 돼.")])

    def test_stale_translation_does_not_replace_latest_source(self) -> None:
        _qapp()
        window = MainWindow()
        window.last_source_text = "Second subtitle"
        window.source_text.setPlainText("Second subtitle")
        window.translation_text.setPlainText(PENDING_TRANSLATION_TEXT)
        window.translation_history = [("First subtitle", ""), ("Second subtitle", "")]

        window._handle_translation_result("First subtitle", "첫 번째 자막")

        self.assertEqual(window.source_text.toPlainText(), "Second subtitle")
        self.assertEqual(window.translation_text.toPlainText(), PENDING_TRANSLATION_TEXT)
        self.assertEqual(
            window.translation_history,
            [("First subtitle", "첫 번째 자막"), ("Second subtitle", "")],
        )
        window.stop()
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
