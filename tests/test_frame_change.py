from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from rtst_app.frame_change import frame_difference, frame_signature, is_visual_change


class FrameChangeTests(unittest.TestCase):
    def test_identical_signatures_have_zero_difference(self) -> None:
        image = Image.new("RGB", (200, 50), "black")

        signature = frame_signature(image)

        self.assertEqual(frame_difference(signature, signature), 0.0)
        self.assertFalse(is_visual_change(signature, signature, threshold=0.5))

    def test_subtitle_like_region_change_is_detected(self) -> None:
        before = Image.new("RGB", (320, 90), "black")
        after = before.copy()
        draw = ImageDraw.Draw(after)
        draw.rectangle((70, 36, 250, 56), fill="white")

        before_signature = frame_signature(before)
        after_signature = frame_signature(after)

        self.assertGreater(frame_difference(before_signature, after_signature), 1.0)
        self.assertTrue(is_visual_change(before_signature, after_signature, threshold=1.0))

    def test_signature_keeps_region_aspect_ratio(self) -> None:
        image = Image.new("RGB", (200, 100), "black")

        signature = frame_signature(image, width=50)

        self.assertEqual(signature.size, (50, 25))


if __name__ == "__main__":
    unittest.main()
