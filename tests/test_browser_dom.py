from __future__ import annotations

import unittest

from typing import Any

from rtst_app.browser_dom import (
    BrowserDomSubtitleReader,
    DEFAULT_SUBTITLE_SELECTORS,
    build_subtitle_script,
    build_tab_probe_script,
    clean_dom_subtitle_text,
)


class FakeReader(BrowserDomSubtitleReader):
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        super().__init__()
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def _send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, params))
        return self.responses.get(method, {})


class FakeTargetReader(BrowserDomSubtitleReader):
    def __init__(self, targets: list[dict[str, Any]], scores: dict[str, int] | None = None) -> None:
        super().__init__()
        self.targets = targets
        self.scores = scores or {}

    def _list_targets(self) -> list[Any]:
        return self.targets

    def _probe_page_score(self, websocket_url: str) -> int:
        return self.scores.get(websocket_url, 0)


class BrowserDomTests(unittest.TestCase):
    def test_script_contains_default_selectors(self) -> None:
        script = build_subtitle_script()

        self.assertIn(DEFAULT_SUBTITLE_SELECTORS[0], script)
        self.assertIn("media.textTracks", script)
        self.assertIn("shadowRoot", script)
        self.assertIn("if (trackParts.length) return trackParts.join", script)

    def test_script_filters_player_controls_from_dom_fallback(self) -> None:
        script = build_subtitle_script()

        self.assertIn("isControlUi", script)
        self.assertIn(".ytp-chrome-top", script)
        self.assertIn(".ytp-title", script)
        self.assertIn("captionAreaCandidate", script)
        self.assertIn("hasSubtitleDescendant", script)

    def test_script_allows_top_positioned_dom_captions(self) -> None:
        script = build_subtitle_script()

        self.assertIn("subtitleVerticalBandTop", script)
        self.assertIn("subtitleVerticalBandBottom", script)
        self.assertIn("insideVideoVerticalBand", script)
        self.assertNotIn("videoRect.height * 0.45", script)

    def test_tab_probe_script_scores_video_and_iframes(self) -> None:
        script = build_tab_probe_script()

        self.assertIn('document.querySelectorAll("video")', script)
        self.assertIn('document.querySelectorAll("iframe")', script)
        self.assertIn(DEFAULT_SUBTITLE_SELECTORS[0], script)

    def test_clean_dom_text_removes_media_progress(self) -> None:
        self.assertEqual(
            clean_dom_subtitle_text("so now I want to jump 0:02 / 19:45 Intro"),
            "so now I want to jump",
        )

    def test_clean_dom_text_keeps_words_after_mid_sentence_progress(self) -> None:
        self.assertEqual(
            clean_dom_subtitle_text("so now 0:02 / 19:45 I want to jump"),
            "so now I want to jump",
        )

    def test_clean_dom_text_collapses_full_phrase_repetition(self) -> None:
        self.assertEqual(
            clean_dom_subtitle_text(
                "so now I want to jump so now I want to jump 0:02 / 19:45 Intro"
            ),
            "so now I want to jump",
        )

    def test_clean_dom_text_does_not_collapse_short_emphasis(self) -> None:
        self.assertEqual(clean_dom_subtitle_text("no no no"), "no no no")

    def test_script_escapes_custom_selector(self) -> None:
        selector = ".caption[data-text=\"a'b\"]"

        script = build_subtitle_script(selector)

        self.assertIn('".caption[data-text=\\\"a\'b\\\"]"', script)
        self.assertIn("const selectors = customSelector ? [customSelector]", script)

    def test_frame_ids_returns_child_frames_only(self) -> None:
        reader = FakeReader(
            {
                "Page.getFrameTree": {
                    "frameTree": {
                        "frame": {"id": "root"},
                        "childFrames": [
                            {"frame": {"id": "child-1"}},
                            {
                                "frame": {"id": "child-2"},
                                "childFrames": [{"frame": {"id": "grandchild"}}],
                            },
                        ],
                    }
                }
            }
        )

        self.assertEqual(reader._frame_ids(), ["child-1", "child-2", "grandchild"])

    def test_create_isolated_world_uses_frame_id(self) -> None:
        reader = FakeReader({"Page.createIsolatedWorld": {"executionContextId": 42}})

        self.assertEqual(reader._create_isolated_world("frame-123"), 42)
        self.assertEqual(reader.calls[0][0], "Page.createIsolatedWorld")
        self.assertEqual(reader.calls[0][1]["frameId"], "frame-123")

    def test_attach_iframe_sessions_attaches_iframe_targets(self) -> None:
        reader = FakeReader(
            {
                "Target.getTargets": {
                    "targetInfos": [
                        {"type": "page", "targetId": "page-1"},
                        {"type": "iframe", "targetId": "iframe-1"},
                    ]
                },
                "Target.attachToTarget": {"sessionId": "session-1"},
            }
        )

        self.assertEqual(reader._attach_iframe_sessions(), ["session-1"])
        self.assertEqual(reader.calls[1][0], "Target.attachToTarget")
        self.assertEqual(reader.calls[1][1]["targetId"], "iframe-1")

    def test_find_target_prefers_probed_media_page_without_filter(self) -> None:
        reader = FakeTargetReader(
            [
                {
                    "type": "page",
                    "title": "New Tab",
                    "url": "chrome://newtab/",
                    "webSocketDebuggerUrl": "ws://tab-a",
                },
                {
                    "type": "page",
                    "title": "Lesson",
                    "url": "https://example.test/lesson",
                    "webSocketDebuggerUrl": "ws://tab-b",
                },
            ],
            scores={"ws://tab-b": 30},
        )

        self.assertEqual(reader._find_target_websocket_url(), "ws://tab-b")

    def test_find_target_filter_takes_precedence_over_probe_score(self) -> None:
        reader = FakeTargetReader(
            [
                {
                    "type": "page",
                    "title": "Target Lesson",
                    "url": "https://example.test/lesson",
                    "webSocketDebuggerUrl": "ws://tab-a",
                },
                {
                    "type": "page",
                    "title": "Video",
                    "url": "https://video.example.test/watch",
                    "webSocketDebuggerUrl": "ws://tab-b",
                },
            ],
            scores={"ws://tab-b": 50},
        )
        reader.tab_filter = "target"

        self.assertEqual(reader._find_target_websocket_url(), "ws://tab-a")


if __name__ == "__main__":
    unittest.main()
