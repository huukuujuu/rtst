from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from rtst_app.oauth_client import (
    TokenBundle,
    code_challenge,
    load_token,
    save_token,
    token_from_response,
)


class OAuthClientTests(unittest.TestCase):
    def test_code_challenge_matches_pkce_example(self) -> None:
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        self.assertEqual(code_challenge(verifier), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_token_validity_uses_expiry(self) -> None:
        self.assertTrue(TokenBundle(access_token="abc", expires_at=time.time() + 120).is_valid())
        self.assertFalse(TokenBundle(access_token="abc", expires_at=time.time() - 1).is_valid())

    def test_token_response_parsing(self) -> None:
        token = token_from_response(
            {
                "access_token": "abc",
                "token_type": "Bearer",
                "refresh_token": "refresh",
                "expires_in": 60,
                "scope": "translate",
            }
        )
        self.assertEqual(token.access_token, "abc")
        self.assertEqual(token.refresh_token, "refresh")
        self.assertEqual(token.scope, "translate")

    def test_token_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "token.json"
            save_token(TokenBundle(access_token="abc", refresh_token="refresh", expires_at=123), path)
            loaded = load_token(path)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.access_token if loaded else "", "abc")


if __name__ == "__main__":
    unittest.main()
