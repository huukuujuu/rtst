from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from rtst_app.ocr import preprocess_subtitle_image, preprocess_subtitle_variants


class OcrPreprocessTests(unittest.TestCase):
    def test_preprocess_upscales_and_converts_to_grayscale(self) -> None:
        image = Image.new("RGB", (100, 30), "black")

        processed = preprocess_subtitle_image(image)

        self.assertEqual(processed.mode, "L")
        self.assertEqual(processed.size, (200, 60))

    def test_preprocess_variants_include_threshold_fallbacks(self) -> None:
        image = Image.new("RGB", (100, 30), "black")
        draw = ImageDraw.Draw(image)
        draw.text((10, 8), "HELLO", fill="white")

        variants = preprocess_subtitle_variants(image)
        names = [name for name, _variant in variants]

        self.assertEqual(
            names,
            ["enhanced", "original", "light_text_threshold", "dark_text_threshold"],
        )
        self.assertTrue(all(variant.mode == "RGB" for _name, variant in variants))
        self.assertEqual(variants[0][1].size, (200, 60))


if __name__ == "__main__":
    unittest.main()
