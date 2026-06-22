from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from rtst_app.oauth_client import OAuthConfig


OPENAI_CODEX_AUTH_CLAIM = "https://api.openai.com/auth"
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CODEX_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
OPENAI_CODEX_MODEL = "gpt-5.5"
OPENAI_CODEX_CALLBACK_HOST = "localhost"
OPENAI_CODEX_CALLBACK_PORT = 1455
OPENAI_CODEX_CALLBACK_PATH = "/auth/callback"
OPENAI_CODEX_TOKEN_PATH = Path("rtst_codex_oauth_token.json")


def codex_oauth_config_from_env() -> OAuthConfig:
    return OAuthConfig(
        authorization_url=os.getenv("RTST_CODEX_OAUTH_AUTH_URL", OPENAI_CODEX_AUTHORIZE_URL),
        token_url=os.getenv("RTST_CODEX_OAUTH_TOKEN_URL", OPENAI_CODEX_TOKEN_URL),
        client_id=os.getenv("RTST_CODEX_OAUTH_CLIENT_ID", OPENAI_CODEX_CLIENT_ID),
        scope=os.getenv("RTST_CODEX_OAUTH_SCOPE", OPENAI_CODEX_SCOPE),
        token_path=Path(os.getenv("RTST_CODEX_OAUTH_TOKEN", str(OPENAI_CODEX_TOKEN_PATH))),
        callback_host=os.getenv("RTST_CODEX_OAUTH_CALLBACK_HOST", OPENAI_CODEX_CALLBACK_HOST),
        callback_port=_env_int("RTST_CODEX_OAUTH_CALLBACK_PORT", OPENAI_CODEX_CALLBACK_PORT),
        callback_path=os.getenv("RTST_CODEX_OAUTH_CALLBACK_PATH", OPENAI_CODEX_CALLBACK_PATH),
        extra_authorization_params={
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": os.getenv("RTST_CODEX_ORIGINATOR", "openclaw"),
        },
    )


def codex_base_url_from_env() -> str:
    return os.getenv("RTST_CODEX_BASE_URL", OPENAI_CODEX_BASE_URL)


def codex_model_from_env() -> str:
    return os.getenv("RTST_CODEX_MODEL", OPENAI_CODEX_MODEL)


def resolve_chatgpt_account_id(access_token: str) -> str | None:
    payload = decode_jwt_payload(access_token)
    claim = payload.get(OPENAI_CODEX_AUTH_CLAIM)
    if not isinstance(claim, dict):
        return None
    account_id = claim.get("chatgpt_account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
        data = json.loads(decoded)
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default
