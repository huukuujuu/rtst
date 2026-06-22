from __future__ import annotations

import unittest

from rtst_app.text_utils import TranslationCache, is_substantial_change, normalize_ocr_text, similarity


class TextUtilsTests(unittest.TestCase):
    def test_normalize_ocr_text_collapses_lines_and_spaces(self) -> None:
        self.assertEqual(
            normalize_ocr_text("  Hello   world \n this is fine ! "),
            "Hello world this is fine!",
        )

    def test_similarity_matches_same_text_with_case_difference(self) -> None:
        self.assertGreater(similarity("Hello, WORLD", "hello, world"), 0.95)

    def test_substantial_change_ignores_nearly_identical_text(self) -> None:
        self.assertFalse(is_substantial_change("I am going home.", "I am going home."))

    def test_substantial_change_accepts_new_sentence(self) -> None:
        self.assertTrue(is_substantial_change("I am going home.", "Where are you going?"))

    def test_translation_cache_evicts_oldest(self) -> None:
        cache = TranslationCache(max_size=2)
        cache.set("one", "하나")
        cache.set("two", "둘")
        cache.set("three", "셋")

        self.assertIsNone(cache.get("one"))
        self.assertEqual(cache.get("two"), "둘")
        self.assertEqual(cache.get("three"), "셋")


if __name__ == "__main__":
    unittest.main()
