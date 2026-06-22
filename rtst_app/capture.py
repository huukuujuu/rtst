from __future__ import annotations

from PIL import Image
from mss import mss

from rtst_app.config import CaptureRegion


class ScreenCapturer:
    def grab_region(self, region: CaptureRegion) -> Image.Image:
        if not region.is_valid():
            raise ValueError("캡처 영역이 너무 작습니다.")

        with mss() as capture:
            raw = capture.grab(region.to_mss_monitor())

        return Image.frombytes("RGB", raw.size, raw.rgb)
