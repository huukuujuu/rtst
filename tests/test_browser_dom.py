from __future__ import annotations

import unittest

from typing import Any

from rtst_app.browser_dom import BrowserDomSubtitleReader, DEFAULT_SUBTITLE_SELECTORS, build_subtitle_script


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


if __name__ == "__main__":
    unittest.main()
