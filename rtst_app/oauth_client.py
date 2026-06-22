from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests


TOKEN_PATH = Path(os.getenv("RTST_OAUTH_TOKEN", "rtst_oauth_token.json"))


class OAuthError(RuntimeError):
    pass


@dataclass(slots=True)
class TokenBundle:
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_at: float = 0
    scope: str = ""

    def is_valid(self, skew_seconds: int = 45) -> bool:
        return bool(self.access_token) and time.time() < self.expires_at - skew_seconds


@dataclass(slots=True)
class OAuthConfig:
    authorization_url: str
    token_url: str
    client_id: str
    scope: str = "translate"
    token_path: Path = TOKEN_PATH
    callback_host: str = "127.0.0.1"
    callback_port: int = 0
    callback_path: str = "/callback"
    callback_timeout_seconds: int = 180
    extra_authorization_params: dict[str, str] = field(default_factory=dict)


class OAuthPkceClient:
    def __init__(self, config: OAuthConfig) -> None:
        self.config = config
        self.token = load_token(config.token_path)

    def has_valid_token(self) -> bool:
        return self.token is not None and self.token.is_valid()

    def get_access_token(self) -> str:
        if self.token and self.token.is_valid():
            return self.token.access_token
        if self.token and self.token.refresh_token:
            try:
                self.refresh()
            except OAuthError:
                self.token = None
        if not self.token or not self.token.is_valid():
            self.login()
        if not self.token:
            raise OAuthError("OAuth login did not return a token.")
        return self.token.access_token

    def login(self) -> TokenBundle:
        verifier = generate_code_verifier()
        challenge = code_challenge(verifier)
        state = secrets.token_urlsafe(24)
        callback = _CallbackCapture(state)

        callback_path = _normalize_callback_path(self.config.callback_path)
        server = ThreadingHTTPServer(
            (self.config.callback_host, self.config.callback_port),
            callback.handler_class(callback_path),
        )
        redirect_uri = (
            f"http://{_format_host_for_url(self.config.callback_host)}:"
            f"{server.server_port}{callback_path}"
        )
        server.timeout = 0.5

        thread = threading.Thread(
            target=_serve_until_callback,
            args=(server, callback, self.config.callback_timeout_seconds),
            daemon=True,
        )
        thread.start()

        query = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.config.scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        query.update(self.config.extra_authorization_params)
        webbrowser.open(f"{self.config.authorization_url}?{urllib.parse.urlencode(query)}")

        thread.join(self.config.callback_timeout_seconds + 2)
        server.server_close()

        if callback.error:
            raise OAuthError(callback.error)
        if not callback.code:
            raise OAuthError("Timed out waiting for OAuth callback.")

        self.token = self._exchange_code(callback.code, verifier, redirect_uri)
        save_token(self.token, self.config.token_path)
        return self.token

    def refresh(self) -> TokenBundle:
        if not self.token or not self.token.refresh_token:
            raise OAuthError("No refresh token is available.")

        data = self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": self.token.refresh_token,
                "client_id": self.config.client_id,
            }
        )
        self.token = token_from_response(data, fallback_scope=self.config.scope)
        save_token(self.token, self.config.token_path)
        return self.token

    def clear_token(self) -> None:
        self.token = None
        try:
            self.config.token_path.unlink()
        except FileNotFoundError:
            pass

    def _exchange_code(self, code: str, verifier: str, redirect_uri: str) -> TokenBundle:
        data = self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.config.client_id,
                "code_verifier": verifier,
            }
        )
        return token_from_response(data, fallback_scope=self.config.scope)

    def _post_token(self, payload: dict[str, str]) -> dict[str, Any]:
        try:
            response = requests.post(self.config.token_url, data=payload, timeout=20)
        except requests.RequestException as exc:
            raise OAuthError(f"OAuth token request failed: {exc}") from exc

        if response.status_code >= 400:
            raise OAuthError(f"OAuth token request failed: {response.status_code} {response.text[:500]}")

        try:
            data = response.json()
        except ValueError as exc:
            raise OAuthError("OAuth token response was not JSON.") from exc
        if not isinstance(data, dict):
            raise OAuthError("OAuth token response had an unexpected shape.")
        return data


def generate_code_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def token_from_response(data: dict[str, Any], fallback_scope: str = "") -> TokenBundle:
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthError("OAuth token response did not include access_token.")

    expires_in = data.get("expires_in", 3600)
    try:
        expires_at = time.time() + int(expires_in)
    except (TypeError, ValueError):
        expires_at = time.time() + 3600

    refresh_token = data.get("refresh_token")
    scope = data.get("scope")
    token_type = data.get("token_type", "Bearer")
    return TokenBundle(
        access_token=access_token,
        token_type=token_type if isinstance(token_type, str) else "Bearer",
        refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        expires_at=expires_at,
        scope=scope if isinstance(scope, str) else fallback_scope,
    )


def load_token(path: Path = TOKEN_PATH) -> TokenBundle | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    access_token = data.get("access_token")
    if not isinstance(access_token, str):
        return None
    return TokenBundle(
        access_token=access_token,
        token_type=str(data.get("token_type") or "Bearer"),
        refresh_token=data.get("refresh_token") if isinstance(data.get("refresh_token"), str) else None,
        expires_at=float(data.get("expires_at") or 0),
        scope=str(data.get("scope") or ""),
    )


def save_token(token: TokenBundle, path: Path = TOKEN_PATH) -> None:
    path.write_text(json.dumps(asdict(token), indent=2) + "\n", encoding="utf-8")


class _CallbackCapture:
    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self.code: str | None = None
        self.error: str | None = None
        self.done = threading.Event()

    def handler_class(self, callback_path: str) -> type[BaseHTTPRequestHandler]:
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != callback_path:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html><body><h1>RTST callback not found</h1></body></html>")
                    return

                values = urllib.parse.parse_qs(parsed.query)
                state = values.get("state", [""])[0]
                code = values.get("code", [""])[0]
                error = values.get("error", [""])[0]

                if error:
                    capture.error = error
                elif state != capture.expected_state:
                    capture.error = "OAuth state did not match."
                elif not code:
                    capture.error = "OAuth callback did not include code."
                else:
                    capture.code = code

                capture.done.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>RTST login complete</h1>"
                    b"<p>You can close this browser tab.</p></body></html>"
                )

            def log_message(self, _format: str, *_args: object) -> None:
                return

        return Handler


def _serve_until_callback(
    server: ThreadingHTTPServer,
    callback: _CallbackCapture,
    timeout_seconds: int,
) -> None:
    deadline = time.time() + timeout_seconds
    while not callback.done.is_set() and time.time() < deadline:
        server.handle_request()


def _normalize_callback_path(path: str) -> str:
    normalized = path.strip() or "/callback"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def _format_host_for_url(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host
