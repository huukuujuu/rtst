from __future__ import annotations

import unittest

from rtst_app.app import append_pending_history_entry, complete_history_entry


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

    def test_completed_duplicate_is_not_appended(self) -> None:
        history: list[tuple[str, str]] = [("No way.", "말도 안 돼.")]

        self.assertFalse(complete_history_entry(history, "No way.", "말도 안 돼."))
        self.assertEqual(history, [("No way.", "말도 안 돼.")])


if __name__ == "__main__":
    unittest.main()
