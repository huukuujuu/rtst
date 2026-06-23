from __future__ import annotations

import unittest

from rtst_app.config import _coerce_settings


class ConfigTests(unittest.TestCase):
    def test_overlay_defaults_are_compact(self) -> None:
        settings = _coerce_settings({})

        self.assertEqual(settings.overlay_width, 600)
        self.assertEqual(settings.overlay_max_height, 600)
        self.assertEqual(settings.overlay_font_size, 15)

    def test_overlay_manual_position_is_preserved(self) -> None:
        settings = _coerce_settings(
            {
                "overlay_position": "manual",
                "overlay_manual_x": 123,
                "overlay_manual_y": 456,
            }
        )

        self.assertEqual(settings.overlay_position, "manual")
        self.assertEqual(settings.overlay_manual_x, 123)
        self.assertEqual(settings.overlay_manual_y, 456)

    def test_invalid_overlay_position_falls_back_to_auto(self) -> None:
        settings = _coerce_settings({"overlay_position": "somewhere"})

        self.assertEqual(settings.overlay_position, "auto")

    def test_overlay_history_limit_is_clamped(self) -> None:
        low = _coerce_settings({"overlay_history_limit": 0})
        high = _coerce_settings({"overlay_history_limit": 99})

        self.assertEqual(low.overlay_history_limit, 1)
        self.assertEqual(high.overlay_history_limit, 12)

    def test_overlay_size_is_clamped(self) -> None:
        small = _coerce_settings({"overlay_width": 10, "overlay_max_height": 10})
        large = _coerce_settings({"overlay_width": 9999, "overlay_max_height": 9999})

        self.assertEqual(small.overlay_width, 320)
        self.assertEqual(small.overlay_max_height, 80)
        self.assertEqual(large.overlay_width, 2400)
        self.assertEqual(large.overlay_max_height, 1200)

    def test_translation_history_limit_is_clamped(self) -> None:
        small = _coerce_settings({"translation_history_limit": 1})
        large = _coerce_settings({"translation_history_limit": 9999})

        self.assertEqual(small.translation_history_limit, 20)
        self.assertEqual(large.translation_history_limit, 1000)

    def test_overlay_accumulate_accepts_string_false(self) -> None:
        settings = _coerce_settings({"overlay_accumulate": "false"})

        self.assertFalse(settings.overlay_accumulate)

    def test_overlay_enabled_accepts_string_false(self) -> None:
        settings = _coerce_settings({"overlay_enabled": "false"})

        self.assertFalse(settings.overlay_enabled)


if __name__ == "__main__":
    unittest.main()
