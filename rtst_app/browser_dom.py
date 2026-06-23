from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from rtst_app.logging_utils import clip_text, get_logger
from rtst_app.text_utils import normalize_ocr_text


log = get_logger("browser_dom")

_MEDIA_PROGRESS_PATTERN_SOURCE = (
    r"\b\d{1,2}:\d{2}(?::\d{2})?\s*/\s*"
    r"\d{1,2}:\d{2}(?::\d{2})?"
)
_MEDIA_PROGRESS_PATTERN = re.compile(_MEDIA_PROGRESS_PATTERN_SOURCE)
_MEDIA_PROGRESS_WITH_CHAPTER_AT_END = re.compile(
    _MEDIA_PROGRESS_PATTERN_SOURCE
    + r"(?:\s+(?:Intro|Introduction|Outro|Credits|Chapter\s+\d+))?\s*$",
    re.IGNORECASE,
)
_PLAYER_UI_MARKER = re.compile(
    r"(?:"
    r"음성\s*&\s*자막|"
    r"자막\s*스타일|"
    r"자막\s*없음|자막없음|"
    r"영어\s*한국어|영어한국어|"
    r"audio\s*(?:&|and)\s*(?:subtitles|captions)|"
    r"(?:subtitle|caption)\s*style|"
    r"(?:subtitles|captions)\s*off"
    r")",
    re.IGNORECASE,
)
_PLAYER_UI_PREFIX_TAIL = re.compile(r"\s*[:：]\s*\d+\.\s*[^:：]{0,80}$")
_NUMBERED_PLAYER_UI_TAIL = re.compile(r"\s*[:：]\s*\d+\.\s*(?P<tail>[^:：]{1,120})$")
_LANGUAGE_UI_TAILS = ("영어", "한국어", "일본어", "중국어")
_TRAILING_LANGUAGE_UI_TAIL = re.compile(
    r"\s+(?P<tail>"
    + "|".join(
        re.escape(tail)
        for tail in (
            *_LANGUAGE_UI_TAILS,
            *(tail.encode("utf-8").decode("latin-1") for tail in _LANGUAGE_UI_TAILS),
        )
    )
    + r")\s*$",
    re.IGNORECASE,
)

DEFAULT_SUBTITLE_SELECTORS = [
    ".ytp-caption-segment",
    ".caption-window",
    ".captions-text",
    ".vjs-text-track-display",
    ".vjs-text-track-cue",
    ".jw-text-track-container",
    "[role='caption']",
    "[aria-live='polite']",
    "[aria-live='assertive']",
    "[class*='caption' i]",
    "[class*='subtitle' i]",
    "[class*='subtitles' i]",
    "[class*='timedtext' i]",
]

MEDIA_TAB_KEYWORDS = (
    "youtube",
    "youtu.be",
    "netflix",
    "disney",
    "primevideo",
    "hulu",
    "vimeo",
    "twitch",
    "ted",
    "coursera",
    "udemy",
    "khan",
    "player",
    "video",
    "watch",
    "embed",
)


class BrowserDomError(RuntimeError):
    pass


@dataclass(slots=True)
class BrowserDomSubtitleReader:
    debug_url: str = "http://127.0.0.1:9222"
    tab_filter: str = ""
    subtitle_selector: str = ""
    timeout_seconds: float = 3.0
    _websocket: Any | None = field(default=None, init=False, repr=False)
    _message_id: int = field(default=0, init=False, repr=False)
    _target_websocket_url: str = field(default="", init=False, repr=False)

    def ensure_ready(self) -> None:
        self._import_websocket()
        self._target_websocket_url = self._find_target_websocket_url()

    def close(self) -> None:
        if self._websocket is not None:
            try:
                self._websocket.close()
            except Exception:  # noqa: BLE001
                pass
        self._websocket = None

    def read_text(self) -> str:
        script = build_subtitle_script(self.subtitle_selector)
        started_at = time.perf_counter()
        try:
            result = self._evaluate_all_frames(script)
        except BrowserDomError:
            self.close()
            self._target_websocket_url = self._find_target_websocket_url()
            result = self._evaluate_all_frames(script)

        text = clean_dom_subtitle_text(result)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        log.info("dom_subtitle_read read_ms=%.1f text=%r", elapsed_ms, clip_text(text))
        return text

    def _evaluate_all_frames(self, expression: str) -> str:
        parts: list[str] = []
        seen: set[str] = set()

        main_text = self._evaluate(expression)
        if main_text:
            text = clean_dom_subtitle_text(main_text)
            if text and text not in seen:
                seen.add(text)
                parts.append(text)

        for frame_id in self._frame_ids():
            context_id = self._create_isolated_world(frame_id)
            if context_id is None:
                continue
            frame_text = self._evaluate(expression, context_id=context_id)
            text = clean_dom_subtitle_text(frame_text)
            if text and text not in seen:
                seen.add(text)
                parts.append(text)

        for session_id in self._attach_iframe_sessions():
            try:
                frame_text = self._evaluate(expression, session_id=session_id)
                text = clean_dom_subtitle_text(frame_text)
                if text and text not in seen:
                    seen.add(text)
                    parts.append(text)
            finally:
                self._detach_session(session_id)

        return "\n".join(parts)

    def _evaluate(
        self,
        expression: str,
        context_id: int | None = None,
        session_id: str | None = None,
    ) -> str:
        params: dict[str, Any] = {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
        }
        if context_id is not None:
            params["contextId"] = context_id
        result = self._send_command("Runtime.evaluate", params, session_id=session_id)
        if "exceptionDetails" in result:
            raise BrowserDomError(str(result["exceptionDetails"])[:500])
        remote_object = result.get("result")
        if not isinstance(remote_object, dict):
            return ""
        value = remote_object.get("value")
        return value if isinstance(value, str) else ""

    def _send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        websocket = self._connect()
        self._message_id += 1
        message_id = self._message_id
        payload = {
            "id": message_id,
            "method": method,
            "params": params or {},
        }
        if session_id is not None:
            payload["sessionId"] = session_id
        try:
            websocket.send(json.dumps(payload))
            while True:
                raw = websocket.recv()
                data = json.loads(raw)
                if data.get("id") != message_id:
                    continue
                if "error" in data:
                    raise BrowserDomError(str(data["error"]))
                result = data.get("result")
                return result if isinstance(result, dict) else {}
        except BrowserDomError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BrowserDomError(f"Browser DevTools command failed: {exc}") from exc

    def _frame_ids(self) -> list[str]:
        try:
            result = self._send_command("Page.getFrameTree")
        except BrowserDomError as exc:
            log.info("dom_frame_tree_unavailable error=%r", str(exc))
            return []

        root = result.get("frameTree")
        frame_ids: list[str] = []

        def collect(node: object) -> None:
            if not isinstance(node, dict):
                return
            frame = node.get("frame")
            if isinstance(frame, dict):
                frame_id = frame.get("id")
                if isinstance(frame_id, str):
                    frame_ids.append(frame_id)
            children = node.get("childFrames")
            if isinstance(children, list):
                for child in children:
                    collect(child)

        collect(root)
        return frame_ids[1:]

    def _create_isolated_world(self, frame_id: str) -> int | None:
        try:
            result = self._send_command(
                "Page.createIsolatedWorld",
                {
                    "frameId": frame_id,
                    "worldName": "rtst_subtitle_reader",
                    "grantUniveralAccess": True,
                },
            )
        except BrowserDomError as exc:
            log.info("dom_frame_world_unavailable frame_id=%s error=%r", frame_id, str(exc))
            return None

        context_id = result.get("executionContextId")
        return context_id if isinstance(context_id, int) else None

    def _attach_iframe_sessions(self) -> list[str]:
        try:
            result = self._send_command("Target.getTargets")
        except BrowserDomError as exc:
            log.info("dom_targets_unavailable error=%r", str(exc))
            return []

        target_infos = result.get("targetInfos")
        if not isinstance(target_infos, list):
            return []

        session_ids: list[str] = []
        for target in target_infos:
            if not isinstance(target, dict) or target.get("type") != "iframe":
                continue
            target_id = target.get("targetId")
            if not isinstance(target_id, str):
                continue
            try:
                attach_result = self._send_command(
                    "Target.attachToTarget",
                    {"targetId": target_id, "flatten": True},
                )
            except BrowserDomError as exc:
                log.info("dom_iframe_attach_failed target_id=%s error=%r", target_id, str(exc))
                continue
            session_id = attach_result.get("sessionId")
            if isinstance(session_id, str):
                session_ids.append(session_id)
        return session_ids

    def _detach_session(self, session_id: str) -> None:
        try:
            self._send_command("Target.detachFromTarget", {"sessionId": session_id})
        except BrowserDomError as exc:
            log.info("dom_iframe_detach_failed session_id=%s error=%r", session_id, str(exc))

    def _connect(self) -> Any:
        if self._websocket is not None:
            return self._websocket

        websocket_module = self._import_websocket()
        if not self._target_websocket_url:
            self._target_websocket_url = self._find_target_websocket_url()
        try:
            self._websocket = websocket_module.create_connection(
                self._target_websocket_url,
                timeout=self.timeout_seconds,
                suppress_origin=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise BrowserDomError(f"Could not connect to Chrome DevTools websocket: {exc}") from exc
        return self._websocket

    def _find_target_websocket_url(self) -> str:
        targets = self._list_targets()

        pages = [
            target
            for target in targets
            if isinstance(target, dict)
            and target.get("type") == "page"
            and isinstance(target.get("webSocketDebuggerUrl"), str)
        ]
        if not pages:
            raise BrowserDomError("No debuggable Chrome page was found.")

        tab_filter = self.tab_filter.strip().lower()
        if tab_filter:
            for page in pages:
                title = str(page.get("title", "")).lower()
                page_url = str(page.get("url", "")).lower()
                if tab_filter in title or tab_filter in page_url:
                    return str(page["webSocketDebuggerUrl"])
            raise BrowserDomError(f"No Chrome tab matched tab filter: {self.tab_filter}")

        selected = self._select_best_page(pages)
        return str(selected["webSocketDebuggerUrl"])

    def _list_targets(self) -> list[Any]:
        url = self.debug_url.rstrip("/") + "/json/list"
        try:
            response = requests.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
            targets = response.json()
        except requests.RequestException as exc:
            raise BrowserDomError(
                "Could not reach Chrome DevTools. Launch Chrome with "
                "--remote-debugging-port=9222 or use run_rtst_browser_dom.bat."
            ) from exc
        except ValueError as exc:
            raise BrowserDomError("Chrome DevTools target list was not JSON.") from exc

        if not isinstance(targets, list):
            raise BrowserDomError("Chrome DevTools target list was not a list.")

        return targets

    def _select_best_page(self, pages: list[dict[str, Any]]) -> dict[str, Any]:
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, page in enumerate(pages):
            websocket_url = page.get("webSocketDebuggerUrl")
            score = self._static_page_score(page)
            if isinstance(websocket_url, str):
                score += self._probe_page_score(websocket_url)
            scored.append((score, -index, page))

        score, _order, selected = max(scored, key=lambda item: (item[0], item[1]))
        log.info(
            "dom_target_selected score=%s title=%r url=%r",
            score,
            clip_text(str(selected.get("title", ""))),
            clip_text(str(selected.get("url", ""))),
        )
        return selected

    @staticmethod
    def _static_page_score(page: dict[str, Any]) -> int:
        title = str(page.get("title", "")).lower()
        page_url = str(page.get("url", "")).lower()
        text = f"{title} {page_url}"
        score = sum(2 for keyword in MEDIA_TAB_KEYWORDS if keyword in text)
        if page_url.startswith(("chrome://", "devtools://", "edge://", "about:")):
            score -= 5
        return score

    def _probe_page_score(self, websocket_url: str) -> int:
        websocket_module = self._import_websocket()
        timeout = min(max(self.timeout_seconds, 0.5), 1.5)
        websocket = None
        try:
            websocket = websocket_module.create_connection(
                websocket_url,
                timeout=timeout,
                suppress_origin=True,
            )
            websocket.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": build_tab_probe_script(),
                            "returnByValue": True,
                            "awaitPromise": False,
                        },
                    }
                )
            )
            while True:
                data = json.loads(websocket.recv())
                if data.get("id") != 1:
                    continue
                if "error" in data or "exceptionDetails" in data.get("result", {}):
                    return 0
                remote_object = data.get("result", {}).get("result", {})
                value = remote_object.get("value") if isinstance(remote_object, dict) else 0
                return int(value) if isinstance(value, (int, float)) else 0
        except Exception as exc:  # noqa: BLE001
            log.info("dom_target_probe_failed error=%r", str(exc))
            return 0
        finally:
            if websocket is not None:
                try:
                    websocket.close()
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _import_websocket() -> Any:
        try:
            import websocket
        except ModuleNotFoundError as exc:
            raise BrowserDomError(
                "websocket-client is not installed. Run: pip install -r requirements.txt"
            ) from exc
        return websocket


def clean_dom_subtitle_text(text: str) -> str:
    cleaned = _MEDIA_PROGRESS_WITH_CHAPTER_AT_END.sub(" ", text)
    cleaned = _MEDIA_PROGRESS_PATTERN.sub(" ", cleaned)
    cleaned = normalize_ocr_text(cleaned)
    cleaned = _strip_player_ui_noise(cleaned)
    return _collapse_full_word_repetition(cleaned)


def _strip_player_ui_noise(text: str) -> str:
    match = _PLAYER_UI_MARKER.search(text)
    if match is not None:
        prefix = text[: match.start()].rstrip()
        prefix = _PLAYER_UI_PREFIX_TAIL.sub("", prefix)
        return prefix.strip(" :-_")

    tail_match = _NUMBERED_PLAYER_UI_TAIL.search(text)
    if tail_match is not None and _looks_like_player_ui_tail(tail_match.group("tail")):
        prefix = text[: tail_match.start()].rstrip()
        return prefix.strip(" :-_")

    language_tail_match = _TRAILING_LANGUAGE_UI_TAIL.search(text)
    if language_tail_match is None:
        return text
    prefix = text[: language_tail_match.start()].rstrip()
    if not _looks_like_subtitle_prefix_before_ui_tail(prefix):
        return text
    return prefix.strip()


def _looks_like_player_ui_tail(tail: str) -> bool:
    if _PLAYER_UI_MARKER.search(tail):
        return True

    compact = re.sub(r"\s+", "", tail)
    if not compact or len(compact) > 90:
        return False

    hangul_count = sum(1 for char in compact if "\uac00" <= char <= "\ud7a3")
    mojibake_count = sum(1 for char in compact if 0x80 <= ord(char) <= 0xFF)
    return hangul_count >= 2 or mojibake_count >= 2


def _looks_like_subtitle_prefix_before_ui_tail(prefix: str) -> bool:
    stripped = prefix.strip()
    if not stripped or not re.search(r"[A-Za-z]", stripped):
        return False
    return bool(re.search(r"[.!?\"')\]]$", stripped))


def _collapse_full_word_repetition(text: str) -> str:
    words = text.split()
    if len(words) < 6:
        return text

    for segment_length in range(3, (len(words) // 2) + 1):
        if len(words) % segment_length != 0:
            continue
        segment = words[:segment_length]
        if all(
            words[index : index + segment_length] == segment
            for index in range(segment_length, len(words), segment_length)
        ):
            return " ".join(segment)
    return text


def build_subtitle_script(subtitle_selector: str = "") -> str:
    selector_json = json.dumps(subtitle_selector.strip())
    selectors_json = json.dumps(DEFAULT_SUBTITLE_SELECTORS)
    return f"""
(() => {{
  const customSelector = {selector_json};
  const selectors = customSelector ? [customSelector] : {selectors_json};
  const trackSeen = new Set();
  const trackParts = [];
  const domSeen = new Set();
  const domParts = [];

  function clean(value) {{
    return String(value || "")
      .replace(/<[^>]+>/g, " ")
      .replace(/\\s+/g, " ")
      .trim();
  }}

  function add(parts, seen, value) {{
    const text = stripPlayerUiNoise(clean(value));
    if (!text || seen.has(text)) return;
    if (text.length > 500) return;
    seen.add(text);
    parts.push(text);
  }}

  function stripPlayerUiNoise(text) {{
    const marker = /(음성\\s*&\\s*자막|자막\\s*스타일|자막\\s*없음|자막없음|영어\\s*한국어|영어한국어|audio\\s*(?:&|and)\\s*(?:subtitles|captions)|(?:subtitle|caption)\\s*style|(?:subtitles|captions)\\s*off)/i;
    const match = marker.exec(text);
    if (match) {{
      return text
        .slice(0, match.index)
        .replace(/\\s*[:：]\\s*\\d+\\.\\s*[^:：]{{0,80}}$/, "")
        .replace(/[:\\-_\\s]+$/, "")
        .trim();
    }}

    const tailMatch = /\\s*[:：]\\s*\\d+\\.\\s*([^:：]{{1,120}})$/.exec(text);
    if (tailMatch && looksLikePlayerUiTail(tailMatch[1])) {{
      return text.slice(0, tailMatch.index).replace(/[:\\-_\\s]+$/, "").trim();
    }}

    const languageTailMatch = /\\s+(영어|한국어|일본어|중국어)\\s*$/i.exec(text);
    if (!languageTailMatch) return text;
    const prefix = text.slice(0, languageTailMatch.index).trim();
    if (!looksLikeSubtitlePrefixBeforeUiTail(prefix)) return text;
    return prefix;
  }}

  function looksLikePlayerUiTail(tail) {{
    if (/(음성\\s*&\\s*자막|자막\\s*스타일|자막\\s*없음|자막없음|영어\\s*한국어|영어한국어|audio\\s*(?:&|and)\\s*(?:subtitles|captions)|(?:subtitle|caption)\\s*style|(?:subtitles|captions)\\s*off)/i.test(tail)) return true;
    const compact = String(tail || "").replace(/\\s+/g, "");
    if (!compact || compact.length > 90) return false;
    const hangul = compact.match(/[가-힣]/g) || [];
    const mojibake = compact.match(/[\\u0080-\\u00ff]/g) || [];
    return hangul.length >= 2 || mojibake.length >= 2;
  }}

  function looksLikeSubtitlePrefixBeforeUiTail(prefix) {{
    const text = String(prefix || "").trim();
    if (!text || !/[A-Za-z]/.test(text)) return false;
    return /[.!?"')\\]]$/.test(text);
  }}

  function visible(element) {{
    if (!element || !element.getBoundingClientRect) return false;
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      rect.bottom > 0 &&
      rect.right > 0 &&
      rect.top < window.innerHeight &&
      rect.left < window.innerWidth &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Number(style.opacity || "1") > 0
    );
  }}

  function elementTokens(element) {{
    const values = [];
    let node = element;
    let depth = 0;
    while (node && node.nodeType === Node.ELEMENT_NODE && depth < 4) {{
      values.push(node.tagName || "");
      values.push(node.id || "");
      values.push(node.className || "");
      values.push(node.getAttribute("role") || "");
      values.push(node.getAttribute("aria-live") || "");
      values.push(node.getAttribute("aria-label") || "");
      node = node.parentElement;
      depth += 1;
    }}
    return values.join(" ").toLowerCase();
  }}

  function isControlUi(element) {{
    if (!element || !element.closest) return false;
    const controlSelector = [
      "button",
      "a[href]",
      "input",
      "select",
      "textarea",
      "[role='button']",
      "[role='menu']",
      "[role='menuitem']",
      "[role='toolbar']",
      "[role='slider']",
      "[role='dialog']",
      ".ytp-chrome-top",
      ".ytp-chrome-bottom",
      ".ytp-gradient-top",
      ".ytp-gradient-bottom",
      ".ytp-title",
      ".ytp-tooltip",
      ".ytp-menuitem",
      ".ytp-settings-menu",
      ".vjs-control-bar",
      ".jw-controls"
    ].join(",");
    try {{
      if (element.matches(controlSelector) || element.closest(controlSelector)) return true;
    }} catch (_error) {{
      return false;
    }}

    const tokens = elementTokens(element);
    const captionSignal = /(caption|captions|subtitle|subtitles|timedtext|text-track|texttrack|cue|ytp-caption|vjs-text-track|jw-text-track)/i;
    const uiSignal = /(control|controls|toolbar|button|menu|tooltip|settings|title|chapter|progress|volume|play|pause|seek|scrubber|thumbnail|preview|annotation|advert|ad-|brand|logo|watermark|header|headline|ytp-chrome|ytp-gradient|ytp-title|vjs-control|jw-control)/i;
    return uiSignal.test(tokens) && !captionSignal.test(tokens);
  }}

  function captionAreaCandidate(element) {{
    if (customSelector) return true;
    const rect = element.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const videos = Array.from(document.querySelectorAll("video"))
      .filter((video) => visible(video))
      .map((video) => video.getBoundingClientRect())
      .filter((videoRect) => videoRect.width >= 120 && videoRect.height >= 80)
      .sort((a, b) => (b.width * b.height) - (a.width * a.height));

    if (!videos.length) return true;
    const videoRect = videos[0];
    const horizontalOverlap = rect.right >= videoRect.left && rect.left <= videoRect.right;
    const verticalPadding = Math.max(12, videoRect.height * 0.03);
    const subtitleVerticalBandTop = videoRect.top - verticalPadding;
    const subtitleVerticalBandBottom = videoRect.bottom + verticalPadding;
    const insideVideoVerticalBand =
      centerY >= subtitleVerticalBandTop &&
      centerY <= subtitleVerticalBandBottom;
    const centeredOnVideo =
      centerX >= videoRect.left - videoRect.width * 0.05 &&
      centerX <= videoRect.right + videoRect.width * 0.05;
    return horizontalOverlap && insideVideoVerticalBand && centeredOnVideo;
  }}

  function hasSubtitleDescendant(element) {{
    if (customSelector) return false;
    for (const selector of selectors) {{
      let descendants = [];
      try {{
        descendants = Array.from(element.querySelectorAll(selector));
      }} catch (_error) {{
        continue;
      }}
      for (const descendant of descendants) {{
        if (descendant === element) continue;
        if (!visible(descendant)) continue;
        if (isControlUi(descendant)) continue;
        return true;
      }}
    }}
    return false;
  }}

  function subtitleLikeElement(element) {{
    if (customSelector) return !isControlUi(element);
    if (isControlUi(element)) return false;
    if (hasSubtitleDescendant(element)) return false;
    if (!stripPlayerUiNoise(element.innerText || element.textContent || "")) return false;
    const tokens = elementTokens(element);
    const captionSignal = /(caption|captions|subtitle|subtitles|timedtext|text-track|texttrack|cue|ytp-caption|vjs-text-track|jw-text-track|aria-live)/i;
    return captionSignal.test(tokens) && captionAreaCandidate(element);
  }}

  function collectRoots(root, roots) {{
    roots.push(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let node = walker.nextNode();
    while (node) {{
      if (node.shadowRoot) collectRoots(node.shadowRoot, roots);
      node = walker.nextNode();
    }}
  }}

  for (const media of document.querySelectorAll("video,audio")) {{
    for (const track of Array.from(media.textTracks || [])) {{
      const cues = track.activeCues;
      if (!cues) continue;
      for (const cue of Array.from(cues)) {{
        add(trackParts, trackSeen, cue.text || "");
      }}
    }}
  }}

  if (trackParts.length) return trackParts.join("\\n");

  const roots = [];
  collectRoots(document, roots);
  for (const root of roots) {{
    for (const selector of selectors) {{
      let elements = [];
      try {{
        elements = Array.from(root.querySelectorAll(selector));
      }} catch (_error) {{
        continue;
      }}
      for (const element of elements) {{
        if (!visible(element)) continue;
        if (!subtitleLikeElement(element)) continue;
        add(domParts, domSeen, element.innerText || element.textContent || "");
      }}
    }}
  }}

  return domParts.join("\\n");
}})()
""".strip()


def build_tab_probe_script() -> str:
    selectors_json = json.dumps(DEFAULT_SUBTITLE_SELECTORS)
    return f"""
(() => {{
  const selectors = {selectors_json};

  function visible(element) {{
    if (!element || !element.getBoundingClientRect) return false;
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      rect.bottom > 0 &&
      rect.right > 0 &&
      rect.top < window.innerHeight &&
      rect.left < window.innerWidth &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Number(style.opacity || "1") > 0
    );
  }}

  let score = 0;
  const videos = Array.from(document.querySelectorAll("video")).filter(visible);
  score += videos.length * 20;
  for (const video of videos) {{
    for (const track of Array.from(video.textTracks || [])) {{
      score += 4;
      if (track.activeCues && track.activeCues.length) score += 12;
    }}
  }}

  for (const selector of selectors) {{
    try {{
      const matches = Array.from(document.querySelectorAll(selector)).filter(visible);
      score += Math.min(matches.length, 5) * 3;
    }} catch (_error) {{
      continue;
    }}
  }}

  const mediaIframePattern = /(youtube|youtu\\.be|vimeo|player|video|watch|embed|twitch)/i;
  const iframes = Array.from(document.querySelectorAll("iframe")).filter((frame) => {{
    const text = `${{frame.src || ""}} ${{frame.title || ""}} ${{frame.name || ""}}`;
    return mediaIframePattern.test(text);
  }});
  score += iframes.length * 10;

  return score;
}})()
""".strip()
