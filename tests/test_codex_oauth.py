from __future__ import annotations

import base64
import json
import os
import unittest
from unittest.mock import patch

from rtst_app.codex_oauth import resolve_chatgpt_account_id
from rtst_app.translator import (
    _codex_reasoning_effort_from_env,
    _extract_codex_stream_text,
    _resolve_codex_responses_url,
    _subtitle_translation_instructions,
)


def _fake_jwt(payload: dict[str, object]) -> str:
    header = {"alg": "none"}
    encoded_header = _b64url(header)
    encoded_payload = _b64url(payload)
    return f"{encoded_header}.{encoded_payload}."


def _b64url(value: dict[str, object]) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class CodexOAuthTests(unittest.TestCase):
    def test_resolve_chatgpt_account_id_from_jwt_claim(self) -> None:
        token = _fake_jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct_123",
                }
            }
        )

        self.assertEqual(resolve_chatgpt_account_id(token), "acct_123")

    def test_resolve_codex_responses_url(self) -> None:
        self.assertEqual(
            _resolve_codex_responses_url("https://chatgpt.com/backend-api"),
            "https://chatgpt.com/backend-api/codex/responses",
        )
        self.assertEqual(
            _resolve_codex_responses_url("https://chatgpt.com/backend-api/codex"),
            "https://chatgpt.com/backend-api/codex/responses",
        )

    def test_codex_reasoning_effort_defaults_to_low(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != "RTST_CODEX_REASONING_EFFORT"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_codex_reasoning_effort_from_env(), "low")

    def test_codex_reasoning_effort_maps_minimal_to_low(self) -> None:
        with patch.dict(os.environ, {"RTST_CODEX_REASONING_EFFORT": "minimal"}):
            self.assertEqual(_codex_reasoning_effort_from_env(), "low")

    def test_codex_reasoning_effort_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {"RTST_CODEX_REASONING_EFFORT": "off"}):
            self.assertEqual(_codex_reasoning_effort_from_env(), "")

    def test_translation_instructions_ignore_player_ui_noise(self) -> None:
        instructions = _subtitle_translation_instructions("English", "Korean")

        self.assertIn("subtitle localizer", instructions)
        self.assertIn("intended meaning", instructions)
        self.assertIn("word-for-word translation", instructions)
        self.assertIn("idioms, phrasal", instructions)
        self.assertIn("natural spoken Korean", instructions)
        self.assertIn("extracted DOM text", instructions)
        self.assertIn("player UI", instructions)
        self.assertIn("titles, buttons, menus", instructions)

    def test_extract_codex_stream_text_from_deltas(self) -> None:
        lines = [
            'data: {"type":"response.output_text.delta","delta":"안녕"}',
            "",
            'data: {"type":"response.output_text.delta","delta":"하세요"}',
            "",
            "data: [DONE]",
        ]

        self.assertEqual(_extract_codex_stream_text(lines), "안녕하세요")

    def test_extract_codex_stream_text_returns_on_text_done(self) -> None:
        lines = [
            'data: {"type":"response.output_text.delta","delta":"draft"}',
            "",
            'data: {"type":"response.output_text.done","text":"완료"}',
            "",
            'data: {"type":"error","message":"late event should not be read"}',
            "",
        ]

        self.assertEqual(_extract_codex_stream_text(lines), "완료")

    def test_extract_codex_stream_text_prefers_completed_response(self) -> None:
        lines = [
            'data: {"type":"response.output_text.delta","delta":"draft"}',
            "",
            (
                'data: {"type":"response.completed","response":{"output":['
                '{"content":[{"text":"완료"}]}]}}'
            ),
            "",
        ]

        self.assertEqual(_extract_codex_stream_text(lines), "완료")


if __name__ == "__main__":
    unittest.main()
