from __future__ import annotations

import html
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from rtst_app.oauth_client import code_challenge
from rtst_app.translator import OpenAITranslator, TranslationError


load_dotenv()

app = FastAPI(title="RTST OAuth Translation Proxy")


@dataclass(slots=True)
class AuthCodeRecord:
    client_id: str
    redirect_uri: str
    scope: str
    code_challenge: str
    expires_at: float
    username: str


@dataclass(slots=True)
class AccessTokenRecord:
    username: str
    scope: str
    expires_at: float


AUTH_CODES: dict[str, AuthCodeRecord] = {}
ACCESS_TOKENS: dict[str, AccessTokenRecord] = {}
REFRESH_TOKENS: dict[str, AccessTokenRecord] = {}


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    source_language: str = "English"
    target_language: str = "Korean"


def _username() -> str:
    return os.getenv("RTST_OAUTH_USERNAME", "rtst")


def _password() -> str:
    return os.getenv("RTST_OAUTH_PASSWORD", "change-me")


def _model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-5-mini")


def _access_token_ttl() -> int:
    return int(os.getenv("RTST_ACCESS_TOKEN_TTL", "3600"))


def _auth_code_ttl() -> int:
    return int(os.getenv("RTST_AUTH_CODE_TTL", "300"))


def _form_value(values: dict[str, list[str]], key: str) -> str:
    return values.get(key, [""])[0]


async def _read_form(request: Request) -> dict[str, list[str]]:
    body = (await request.body()).decode("utf-8")
    return urllib.parse.parse_qs(body, keep_blank_values=True)


def _error_redirect(redirect_uri: str, state: str, error: str) -> RedirectResponse:
    query = {"error": error}
    if state:
        query["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(redirect_uri + separator + urllib.parse.urlencode(query), status_code=302)


def _issue_tokens(username: str, scope: str) -> dict[str, object]:
    access_token = secrets.token_urlsafe(40)
    refresh_token = secrets.token_urlsafe(40)
    expires_in = _access_token_ttl()
    record = AccessTokenRecord(username=username, scope=scope, expires_at=time.time() + expires_in)
    ACCESS_TOKENS[access_token] = record
    REFRESH_TOKENS[refresh_token] = AccessTokenRecord(
        username=username,
        scope=scope,
        expires_at=time.time() + 60 * 60 * 24 * 30,
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "scope": scope,
    }


def _login_page(params: dict[str, str], message: str = "") -> str:
    hidden = "\n".join(
        f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(value)}">'
        for key, value in params.items()
    )
    notice = f"<p>{html.escape(message)}</p>" if message else ""
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <title>RTST OAuth Login</title>
        <style>
          body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 64px auto; }}
          label {{ display: block; margin: 14px 0 6px; }}
          input {{ box-sizing: border-box; width: 100%; padding: 10px; }}
          button {{ margin-top: 18px; padding: 10px 14px; }}
        </style>
      </head>
      <body>
        <h1>RTST Login</h1>
        {notice}
        <form method="post" action="/authorize">
          {hidden}
          <label>Username</label>
          <input name="username" autocomplete="username">
          <label>Password</label>
          <input name="password" type="password" autocomplete="current-password">
          <button type="submit">Authorize RTST</button>
        </form>
      </body>
    </html>
    """


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/authorize", response_class=HTMLResponse)
def authorize(
    response_type: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    scope: str = "translate",
) -> str:
    if response_type != "code":
        raise HTTPException(status_code=400, detail="Only response_type=code is supported.")
    if code_challenge_method != "S256":
        raise HTTPException(status_code=400, detail="Only S256 PKCE is supported.")
    return _login_page(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": scope,
            "code_challenge": code_challenge,
        }
    )


@app.post("/authorize", response_model=None)
async def authorize_submit(request: Request) -> HTMLResponse | RedirectResponse:
    values = await _read_form(request)
    username = _form_value(values, "username")
    password = _form_value(values, "password")
    redirect_uri = _form_value(values, "redirect_uri")
    state = _form_value(values, "state")

    params = {
        "client_id": _form_value(values, "client_id"),
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": _form_value(values, "scope") or "translate",
        "code_challenge": _form_value(values, "code_challenge"),
    }

    if not secrets.compare_digest(username, _username()) or not secrets.compare_digest(password, _password()):
        return HTMLResponse(_login_page(params, "Login failed."), status_code=401)

    if not redirect_uri.startswith("http://127.0.0.1:") and not redirect_uri.startswith("http://localhost:"):
        raise HTTPException(status_code=400, detail="Only loopback redirect URIs are allowed.")

    code = secrets.token_urlsafe(32)
    AUTH_CODES[code] = AuthCodeRecord(
        client_id=params["client_id"],
        redirect_uri=redirect_uri,
        scope=params["scope"],
        code_challenge=params["code_challenge"],
        expires_at=time.time() + _auth_code_ttl(),
        username=username,
    )
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        redirect_uri + separator + urllib.parse.urlencode({"code": code, "state": state}),
        status_code=302,
    )


@app.post("/token")
async def token(request: Request) -> JSONResponse:
    values = await _read_form(request)
    grant_type = _form_value(values, "grant_type")

    if grant_type == "authorization_code":
        code = _form_value(values, "code")
        record = AUTH_CODES.pop(code, None)
        if record is None or record.expires_at < time.time():
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if record.client_id != _form_value(values, "client_id"):
            return JSONResponse({"error": "invalid_client"}, status_code=400)
        if record.redirect_uri != _form_value(values, "redirect_uri"):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if code_challenge(_form_value(values, "code_verifier")) != record.code_challenge:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(_issue_tokens(record.username, record.scope))

    if grant_type == "refresh_token":
        refresh_token = _form_value(values, "refresh_token")
        record = REFRESH_TOKENS.get(refresh_token)
        if record is None or record.expires_at < time.time():
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(_issue_tokens(record.username, record.scope))

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


def require_token(request: Request) -> AccessTokenRecord:
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer token required.")
    record = ACCESS_TOKENS.get(token)
    if record is None or record.expires_at < time.time():
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    if "translate" not in record.scope.split():
        raise HTTPException(status_code=403, detail="Token lacks translate scope.")
    return record


@app.post("/translate")
def translate(
    payload: TranslateRequest,
    _token: Annotated[AccessTokenRecord, Depends(require_token)],
) -> dict[str, str]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured on the proxy.")

    translator = OpenAITranslator(
        api_key=api_key,
        model=_model(),
        source_language=payload.source_language,
        target_language=payload.target_language,
    )
    try:
        translation = translator.translate(payload.text)
    except TranslationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"translation": translation}
