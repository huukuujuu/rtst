from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(os.getenv("RTST_CONFIG", "rtst_settings.json"))


@dataclass(slots=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def is_valid(self) -> bool:
        return self.width >= 20 and self.height >= 10

    def to_mss_monitor(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


@dataclass(slots=True)
class AppSettings:
    subtitle_source: str = "screen_ocr"
    source_language: str = "English"
    target_language: str = "Korean"
    ocr_engine: str = "windows"
    ocr_language: str = "en"
    browser_debug_url: str = "http://127.0.0.1:9222"
    browser_tab_filter: str = ""
    browser_subtitle_selector: str = ""
    translator_provider: str = "openai"
    openai_model: str = "gpt-5-mini"
    codex_base_url: str = "https://chatgpt.com/backend-api"
    oauth_proxy_url: str = "http://127.0.0.1:8787"
    oauth_authorization_url: str = "http://127.0.0.1:8787/authorize"
    oauth_token_url: str = "http://127.0.0.1:8787/token"
    oauth_client_id: str = "rtst-desktop"
    oauth_scope: str = "translate"
    overlay_enabled: bool = True
    overlay_font_size: int = 15
    overlay_opacity: float = 0.9
    overlay_width: int = 600
    overlay_max_height: int = 600
    overlay_position: str = "auto"
    overlay_offset_x: int = 0
    overlay_offset_y: int = 0
    overlay_manual_x: int = -1
    overlay_manual_y: int = -1
    overlay_accumulate: bool = True
    overlay_history_limit: int = 5
    translation_history_limit: int = 200
    show_original: bool = True


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_settings(data: dict[str, Any]) -> AppSettings:
    defaults = asdict(AppSettings())
    defaults.update({key: value for key, value in data.items() if key in defaults})
    settings = AppSettings(**defaults)
    if settings.subtitle_source not in {"screen_ocr", "browser_dom"}:
        settings.subtitle_source = "screen_ocr"
    if settings.overlay_position not in {"auto", "bottom", "top", "center", "custom_region", "manual"}:
        settings.overlay_position = "auto"
    settings.overlay_enabled = _coerce_bool(settings.overlay_enabled, True)
    settings.overlay_font_size = min(max(int(settings.overlay_font_size), 14), 48)
    settings.overlay_opacity = min(max(float(settings.overlay_opacity), 0.3), 1.0)
    settings.overlay_width = min(max(int(settings.overlay_width), 320), 2400)
    settings.overlay_max_height = min(max(int(settings.overlay_max_height), 80), 1200)
    settings.overlay_offset_x = min(max(int(settings.overlay_offset_x), -2000), 2000)
    settings.overlay_offset_y = min(max(int(settings.overlay_offset_y), -2000), 2000)
    settings.overlay_manual_x = min(max(int(settings.overlay_manual_x), -1), 20000)
    settings.overlay_manual_y = min(max(int(settings.overlay_manual_y), -1), 20000)
    settings.overlay_accumulate = _coerce_bool(settings.overlay_accumulate, True)
    settings.overlay_history_limit = min(max(int(settings.overlay_history_limit), 1), 12)
    settings.translation_history_limit = min(max(int(settings.translation_history_limit), 20), 1000)
    return settings


def load_settings(path: Path = CONFIG_PATH) -> AppSettings:
    if not path.exists():
        return AppSettings()

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return AppSettings()

    if not isinstance(data, dict):
        return AppSettings()
    return _coerce_settings(data)


def save_settings(settings: AppSettings, path: Path = CONFIG_PATH) -> None:
    data = asdict(settings)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
