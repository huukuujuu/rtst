from __future__ import annotations

from PIL import Image, ImageChops, ImageStat


_RESAMPLE = Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else Image.BILINEAR


def frame_signature(image: Image.Image, width: int = 64) -> Image.Image:
    if width < 1:
        raise ValueError("Signature width must be positive.")

    source_width = max(image.width, 1)
    height = max(1, round(width * image.height / source_width))
    return image.convert("L").resize((width, height), _RESAMPLE)


def frame_difference(left: Image.Image, right: Image.Image) -> float:
    if left.size != right.size:
        right = right.resize(left.size, _RESAMPLE)

    diff = ImageChops.difference(left, right)
    stat = ImageStat.Stat(diff)
    return float(stat.mean[0])


def is_visual_change(
    previous: Image.Image | None,
    current: Image.Image,
    threshold: float,
) -> bool:
    if previous is None:
        return True
    return frame_difference(previous, current) >= threshold
