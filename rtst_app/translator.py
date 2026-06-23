from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

import requests

from rtst_app.codex_oauth import (
    codex_base_url_from_env,
    codex_model_from_env,
    resolve_chatgpt_account_id,
)
from rtst_app.logging_utils import clip_text, get_logger
from rtst_app.oauth_client import OAuthError, OAuthPkceClient


log = get_logger("translator")


class TranslationError(RuntimeError):
    pass


class BaseTranslator:
    def translate(self, text: str) -> str:
        raise NotImplementedError


def _subtitle_translation_instructions(source_language: str, target_language: str) -> str:
    return (
        "You are a subtitle localizer. "
        f"Translate the intended meaning from {source_language} into {target_language}. "
        "Use short, natural spoken Korean for Korean output. Avoid stiff or "
        "word-for-word translation. Translate idioms, phrasal verbs, slang, and jokes "
        "by meaning. Return only the translated subtitle text. No notes, labels, "
        "quotes, source text, or romanization. If OCR or extracted DOM text is noisy, "
        "translate only the readable subtitle-like part. Ignore obvious player UI "
        "such as titles, buttons, menus, timestamps, and controls."
    )


@dataclass(slots=True)
class MockTranslator(BaseTranslator):
    target_language: str = "Korean"

    def translate(self, text: str) -> str:
        return f"[Mock {self.target_language}] {text}"


@dataclass(slots=True)
class OpenAITranslator(BaseTranslator):
    api_key: str
    model: str
    target_language: str = "Korean"
    source_language: str = "English"
    timeout_seconds: int = 20

    def translate(self, text: str) -> str:
        if not self.api_key:
            raise TranslationError("OPENAI_API_KEY is required.")

        payload: dict[str, Any] = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": _subtitle_translation_instructions(
                        self.source_language,
                        self.target_language,
                    ),
                },
                {
                    "role": "user",
                    "content": f"<subtitle>{text}</subtitle>",
                },
            ],
            "max_output_tokens": 180,
        }

        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TranslationError(f"Translation request failed: {exc}") from exc

        if response.status_code >= 400:
            detail = _extract_error(response)
            raise TranslationError(f"OpenAI API error {response.status_code}: {detail}")

        try:
            data = response.json()
        except ValueError as exc:
            raise TranslationError("OpenAI response was not JSON.") from exc

        translated = _extract_output_text(data)
        if not translated:
            raise TranslationError("OpenAI response did not include translated text.")
        return translated.strip()


@dataclass(slots=True)
class OAuthProxyTranslator(BaseTranslator):
    proxy_url: str
    oauth_client: OAuthPkceClient
    target_language: str = "Korean"
    source_language: str = "English"
    timeout_seconds: int = 20

    def translate(self, text: str) -> str:
        try:
            token = self.oauth_client.get_access_token()
        except OAuthError as exc:
            raise TranslationError(f"OAuth login failed: {exc}") from exc
        return self._translate_with_token(text, token, retry_on_unauthorized=True)

    def _translate_with_token(self, text: str, token: str, retry_on_unauthorized: bool) -> str:
        url = self.proxy_url.rstrip("/") + "/translate"
        payload = {
            "text": text,
            "source_language": self.source_language,
            "target_language": self.target_language,
        }
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TranslationError(f"OAuth proxy request failed: {exc}") from exc

        if response.status_code == 401 and retry_on_unauthorized:
            try:
                self.oauth_client.refresh()
            except OAuthError:
                self.oauth_client.clear_token()
            try:
                refreshed = self.oauth_client.get_access_token()
            except OAuthError as exc:
                raise TranslationError(f"OAuth login failed: {exc}") from exc
            return self._translate_with_token(text, refreshed, retry_on_unauthorized=False)

        if response.status_code >= 400:
            raise TranslationError(f"OAuth proxy error {response.status_code}: {response.text[:500]}")

        try:
            data = response.json()
        except ValueError as exc:
            raise TranslationError("OAuth proxy response was not JSON.") from exc

        translation = data.get("translation")
        if not isinstance(translation, str) or not translation.strip():
            raise TranslationError("OAuth proxy response did not include translation.")
        return translation.strip()


@dataclass(slots=True)
class CodexOAuthTranslator(BaseTranslator):
    oauth_client: OAuthPkceClient
    model: str
    codex_base_url: str
    target_language: str = "Korean"
    source_language: str = "English"
    timeout_seconds: int = 45

    def translate(self, text: str) -> str:
        try:
            token = self.oauth_client.get_access_token()
        except OAuthError as exc:
            raise TranslationError(f"OpenAI Codex OAuth login failed: {exc}") from exc
        return self._translate_with_token(text, token, retry_on_unauthorized=True)

    def _translate_with_token(self, text: str, token: str, retry_on_unauthorized: bool) -> str:
        account_id = resolve_chatgpt_account_id(token)
        if not account_id:
            raise TranslationError("OpenAI Codex OAuth token did not include a ChatGPT account id.")

        payload: dict[str, Any] = {
            "model": self.model,
            "store": False,
            "stream": True,
            "instructions": _subtitle_translation_instructions(
                self.source_language,
                self.target_language,
            ),
            "input": [
                {
                    "role": "user",
                    "content": f"<subtitle>{text}</subtitle>",
                }
            ],
            "text": {"verbosity": "low"},
        }
        max_output_tokens = _env_optional_int("RTST_CODEX_MAX_OUTPUT_TOKENS")
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens

        reasoning_effort = _codex_reasoning_effort_from_env()
        if reasoning_effort:
            reasoning_summary = os.getenv("RTST_CODEX_REASONING_SUMMARY", "auto").strip().lower()
            if reasoning_summary not in {"auto", "concise", "detailed"}:
                reasoning_summary = "auto"
            payload["reasoning"] = {"effort": reasoning_effort, "summary": reasoning_summary}

        request_started_at = time.perf_counter()
        try:
            response = requests.post(
                _resolve_codex_responses_url(self.codex_base_url),
                headers={
                    "Authorization": f"Bearer {token}",
                    "chatgpt-account-id": account_id,
                    "originator": os.getenv("RTST_CODEX_ORIGINATOR", "openclaw"),
                    "User-Agent": "rtst (python)",
                    "OpenAI-Beta": "responses=experimental",
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                },
                json=payload,
                stream=True,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TranslationError(f"OpenAI Codex request failed: {exc}") from exc

        try:
            status_ms = (time.perf_counter() - request_started_at) * 1000
            log.info(
                "codex_response_headers status=%s header_ms=%.1f model=%s source=%r",
                response.status_code,
                status_ms,
                self.model,
                clip_text(text),
            )
            if response.status_code == 401 and retry_on_unauthorized:
                try:
                    self.oauth_client.refresh()
                except OAuthError:
                    self.oauth_client.clear_token()
                try:
                    refreshed = self.oauth_client.get_access_token()
                except OAuthError as exc:
                    raise TranslationError(f"OpenAI Codex OAuth login failed: {exc}") from exc
                return self._translate_with_token(text, refreshed, retry_on_unauthorized=False)

            if response.status_code >= 400:
                detail = _extract_error(response)
                raise TranslationError(f"OpenAI Codex API error {response.status_code}: {detail}")

            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    data = response.json()
                except ValueError as exc:
                    raise TranslationError("OpenAI Codex response was not JSON.") from exc
                translated = _extract_output_text(data)
            else:
                translated = _extract_codex_stream_text(response.iter_lines(decode_unicode=True))
        finally:
            response.close()

        total_ms = (time.perf_counter() - request_started_at) * 1000
        log.info(
            "codex_translation_done total_ms=%.1f source=%r translation=%r",
            total_ms,
            clip_text(text),
            clip_text(translated),
        )

        if not translated:
            raise TranslationError("OpenAI Codex response did not include translated text.")
        return translated.strip()


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _codex_reasoning_effort_from_env() -> str:
    raw = os.getenv("RTST_CODEX_REASONING_EFFORT", "off").strip().lower()
    if raw in {"", "0", "false", "no", "off"}:
        return ""
    aliases = {
        "minimal": "low",
        "min": "low",
        "x-high": "xhigh",
        "extra-high": "xhigh",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in {"none", "low", "medium", "high", "xhigh"}:
        log.warning("codex_reasoning_effort_invalid value=%r fallback=low", raw)
        return "low"
    return normalized


def build_translator(
    provider: str,
    target_language: str,
    source_language: str,
    model: str,
    api_key: str | None = None,
    oauth_proxy_url: str | None = None,
    oauth_client: OAuthPkceClient | None = None,
    codex_base_url: str | None = None,
) -> BaseTranslator:
    provider_key = provider.strip().lower()
    if provider_key == "mock":
        return MockTranslator(target_language=target_language)
    if provider_key == "openai":
        return OpenAITranslator(
            api_key=api_key if api_key is not None else os.getenv("OPENAI_API_KEY", ""),
            model=model or os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            target_language=target_language,
            source_language=source_language,
        )
    if provider_key == "oauth_proxy":
        if oauth_client is None:
            raise TranslationError("OAuth proxy mode requires an OAuth client.")
        if not oauth_proxy_url:
            raise TranslationError("OAuth proxy mode requires a proxy URL.")
        return OAuthProxyTranslator(
            proxy_url=oauth_proxy_url,
            oauth_client=oauth_client,
            target_language=target_language,
            source_language=source_language,
        )
    if provider_key == "codex_oauth":
        if oauth_client is None:
            raise TranslationError("OpenAI Codex OAuth mode requires an OAuth client.")
        return CodexOAuthTranslator(
            oauth_client=oauth_client,
            model=model or codex_model_from_env(),
            codex_base_url=codex_base_url or codex_base_url_from_env(),
            target_language=target_language,
            source_language=source_language,
        )
    raise TranslationError(f"Unsupported translator provider: {provider}")


def _extract_error(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:500]

    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return str(data)[:500]


def _extract_output_text(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text

    parts: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)

    return "\n".join(part.strip() for part in parts if part.strip())


def _resolve_codex_responses_url(base_url: str) -> str:
    normalized = (base_url or "https://chatgpt.com/backend-api").rstrip("/")
    if normalized.endswith("/codex/responses"):
        return normalized
    if normalized.endswith("/codex"):
        return f"{normalized}/responses"
    return f"{normalized}/codex/responses"


def _extract_codex_stream_text(lines: Iterable[str | bytes]) -> str:
    parts: list[str] = []
    completed_text = ""
    for event in _iter_sse_json_events(lines):
        event_type = event.get("type")
        if event_type == "error":
            message = event.get("message") or event.get("code") or str(event)
            raise TranslationError(f"OpenAI Codex stream error: {message}")
        if event_type == "response.failed":
            response = event.get("response")
            error = response.get("error") if isinstance(response, dict) else None
            if isinstance(error, dict):
                message = error.get("message") or error.get("code") or str(error)
            else:
                message = str(event)
            raise TranslationError(f"OpenAI Codex response failed: {message}")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                parts.append(delta)
            continue
        if event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
            continue
        if event_type in {"response.completed", "response.done", "response.incomplete"}:
            response = event.get("response")
            if isinstance(response, dict):
                text = _extract_output_text(response)
                if text.strip():
                    completed_text = text

    return (completed_text or "".join(parts)).strip()


def _iter_sse_json_events(lines: Iterable[str | bytes]) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
        line = line.rstrip("\r\n")
        if not line:
            yield from _flush_sse_data_lines(data_lines)
            data_lines = []
            continue
        if line.startswith("data:"):
            value = line[5:].lstrip()
            if value == "[DONE]":
                break
            data_lines.append(value)
    yield from _flush_sse_data_lines(data_lines)


def _flush_sse_data_lines(data_lines: list[str]) -> Iterator[dict[str, Any]]:
    if not data_lines:
        return
    payload = "\n".join(data_lines).strip()
    if not payload:
        return
    try:
        data = json.loads(payload)
    except ValueError:
        return
    if isinstance(data, dict):
        yield data
