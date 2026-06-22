from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from PIL import Image, ImageOps

from rtst_app.logging_utils import clip_text, get_logger
from rtst_app.text_utils import normalize_ocr_text


log = get_logger("ocr")
_RESAMPLE = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS


class OcrError(RuntimeError):
    pass


class BaseOcr:
    def ensure_ready(self) -> None:
        raise NotImplementedError

    def recognize(self, image: Image.Image) -> str:
        raise NotImplementedError


def preprocess_subtitle_image(image: Image.Image, upscale: int = 2) -> Image.Image:
    gray = image.convert("L")
    if upscale > 1:
        gray = gray.resize((gray.width * upscale, gray.height * upscale), _RESAMPLE)
    return ImageOps.autocontrast(gray)


def _binary_variant(image: Image.Image, threshold: int, invert: bool = False) -> Image.Image:
    binary = image.point(lambda value: 255 if value >= threshold else 0).convert("L")
    if invert:
        binary = ImageOps.invert(binary)
    return binary.convert("RGB")


def preprocess_subtitle_variants(image: Image.Image) -> list[tuple[str, Image.Image]]:
    enhanced = preprocess_subtitle_image(image)
    variants = [
        ("enhanced", enhanced.convert("RGB")),
        ("original", image.convert("RGB")),
        ("light_text_threshold", _binary_variant(enhanced, 155)),
        ("dark_text_threshold", _binary_variant(enhanced, 155, invert=True)),
    ]
    return variants


def _winocr_text(result: object) -> str:
    if not isinstance(result, dict):
        return ""

    text = result.get("text")
    if isinstance(text, str):
        return normalize_ocr_text(text)

    lines = result.get("lines")
    if isinstance(lines, list):
        return normalize_ocr_text(
            " ".join(line.get("text", "") for line in lines if isinstance(line, dict))
        )
    return ""


@dataclass(slots=True)
class WindowsOcr(BaseOcr):
    language: str = "en"

    def ensure_ready(self) -> None:
        try:
            import winocr  # noqa: F401
        except ModuleNotFoundError as exc:
            raise OcrError("winocr is not installed. Run: pip install -r requirements.txt") from exc

    def recognize(self, image: Image.Image) -> str:
        self.ensure_ready()

        import winocr

        attempted: list[str] = []
        for variant_name, processed in preprocess_subtitle_variants(image):
            attempted.append(variant_name)
            try:
                result = winocr.recognize_pil_sync(processed, self.language)
            except AssertionError as exc:
                raise OcrError(
                    f"Windows OCR language is not available: {self.language}. "
                    "Install the OCR language pack in Windows settings or change OCR language."
                ) from exc

            text = _winocr_text(result)
            if text:
                log.info(
                    "ocr_result engine=windows variant=%s text=%r",
                    variant_name,
                    clip_text(text),
                )
                return text

        log.info("ocr_empty engine=windows variants=%s", ",".join(attempted))
        return ""


@dataclass(slots=True)
class TesseractOcr(BaseOcr):
    language: str = "eng"

    def ensure_ready(self) -> None:
        try:
            import pytesseract
        except ModuleNotFoundError as exc:
            raise OcrError("pytesseract is not installed. Run: pip install -r requirements.txt") from exc

        configured_cmd = os.getenv("TESSERACT_CMD", "").strip()
        if configured_cmd:
            pytesseract.pytesseract.tesseract_cmd = configured_cmd

        command = configured_cmd or shutil.which("tesseract")
        if not command:
            raise OcrError("Could not find tesseract.exe. Check PATH or TESSERACT_CMD.")

    def recognize(self, image: Image.Image) -> str:
        self.ensure_ready()

        import pytesseract

        attempted: list[str] = []
        configs = [
            ("block", "--oem 3 --psm 6"),
            ("line", "--oem 3 --psm 7"),
        ]
        for variant_name, processed in preprocess_subtitle_variants(image):
            for config_name, config in configs:
                attempted.append(f"{variant_name}:{config_name}")
                raw_text = pytesseract.image_to_string(processed, lang=self.language, config=config)
                text = normalize_ocr_text(raw_text)
                if text:
                    log.info(
                        "ocr_result engine=tesseract variant=%s config=%s text=%r",
                        variant_name,
                        config_name,
                        clip_text(text),
                    )
                    return text

        log.info("ocr_empty engine=tesseract variants=%s", ",".join(attempted))
        return ""


def build_ocr_engine(engine: str, language: str) -> BaseOcr:
    engine_key = engine.strip().lower()
    language = language.strip()
    if engine_key == "windows":
        return WindowsOcr(language=language or "en")
    if engine_key == "tesseract":
        if language == "en":
            language = "eng"
        return TesseractOcr(language=language or "eng")
    raise OcrError(f"Unsupported OCR engine: {engine}")
