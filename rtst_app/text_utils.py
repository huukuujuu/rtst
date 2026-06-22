from __future__ import annotations

import re
from collections import OrderedDict
from difflib import SequenceMatcher


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.!?;:])")
_REPEATED_SPACES = re.compile(r"\s+")


def normalize_ocr_text(text: str) -> str:
    text = _CONTROL_CHARS.sub(" ", text)
    text = text.replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    joined = " ".join(lines)
    joined = joined.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    joined = _REPEATED_SPACES.sub(" ", joined)
    joined = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", joined)
    return joined.strip(" -_\t\n")


def similarity(left: str, right: str) -> float:
    left_norm = normalize_ocr_text(left).lower()
    right_norm = normalize_ocr_text(right).lower()
    if not left_norm and not right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def is_substantial_change(previous: str, current: str, threshold: float = 0.86) -> bool:
    current_norm = normalize_ocr_text(current)
    if len(current_norm) < 2:
        return False

    previous_norm = normalize_ocr_text(previous)
    if not previous_norm:
        return True

    return similarity(previous_norm, current_norm) < threshold


class TranslationCache:
    def __init__(self, max_size: int = 128) -> None:
        self.max_size = max_size
        self._items: OrderedDict[str, str] = OrderedDict()

    def get(self, source: str) -> str | None:
        key = normalize_ocr_text(source).lower()
        if key not in self._items:
            return None
        self._items.move_to_end(key)
        return self._items[key]

    def set(self, source: str, translation: str) -> None:
        key = normalize_ocr_text(source).lower()
        if not key:
            return
        self._items[key] = translation
        self._items.move_to_end(key)
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()
