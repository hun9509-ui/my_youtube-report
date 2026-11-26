"""Gemini GenerateContent 호출과 공통 응답 처리."""
import json
import os
import re
import time
from functools import lru_cache
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession
import requests

import config


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def safe_error(error):
    message = str(error)
    for secret in (config.YOUTUBE_API_KEY, config.GEMINI_API_KEY, config.DEEPSEEK_API_KEY,
                   config.SUPABASE_KEY, config.TELEGRAM_BOT_TOKEN, os.getenv("DATABASE_URL")):
        if secret:
            message = message.replace(secret, "[redacted]")
    message = re.sub(r"""([?&]key=)[^&\s"'<>]+""", r"\1[redacted]", message)
    return message[:500]


def has_text(result, field):
    return (
        isinstance(result, dict)
        and "error" not in result
        and isinstance(result.get(field), str)
        and bool(result[field].strip())
    )


@lru_cache(maxsize=1)
def _session():
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return AuthorizedSession(credentials)


@lru_cache(maxsize=1)
def _ai_studio_session():
    return requests.Session()


def _post(provider, model, payload):
    model_path = quote(model, safe="")
    if provider == "ai_studio":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_path}:generateContent"
        return _ai_studio_session().post(
            url, headers={"x-goog-api-key": config.GEMINI_API_KEY},
            json=payload, timeout=(10, 90),
        )

    location = config.GOOGLE_CLOUD_LOCATION
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    project = quote(config.GOOGLE_CLOUD_PROJECT, safe="")
    url = (f"https://{host}/v1/projects/{project}/locations/{location}"
           f"/publishers/google/models/{model_path}:generateContent")
    return _session().post(url, json=payload, timeout=(10, 90))


def _response_text(body):
    candidates = body.get("candidates") or []
    if not candidates:
        reason = body.get("promptFeedback", {}).get("blockReason", "NO_CANDIDATES")
        raise ValueError(f"Gemini 응답 없음 ({reason})")
    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    if finish_reason and finish_reason != "STOP":
        raise ValueError(f"Gemini 응답 종료 ({finish_reason})")
    content = "".join(
        part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
        if not part.get("thought")
    )
    if not content.strip():
        raise ValueError("Gemini 응답 텍스트 없음")
    return content


class _GenerationError(RuntimeError):
    def __init__(self, message, model):
        super().__init__(message)
        self.model = model


def _generate(provider, model, fallback_models, attempts, parts, generation_config, parse):
    if provider == "ai_studio" and not config.GEMINI_API_KEY:
        raise _GenerationError("GEMINI_API_KEY 미설정", model)
    if provider not in {"ai_studio", "vertexai"}:
        raise _GenerationError("GEMINI_PROVIDER는 ai_studio 또는 vertexai이어야 합니다", model)
    if config.GEMINI_THINKING_LEVEL:
        generation_config["thinkingConfig"] = {"thinkingLevel": config.GEMINI_THINKING_LEVEL}
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": generation_config,
    }
    attempts = max(1, attempts)
    for current_model in dict.fromkeys([model, *(fallback_models or [])]):
        for attempt in range(1, attempts + 1):
            try:
                response = _post(provider, current_model, payload)
                if response.ok:
                    body = response.json()
                    return parse(_response_text(body)), current_model, body.get("modelVersion")
                status = response.status_code
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                detail = body.get("error") if isinstance(body, dict) else None
                message = detail.get("message") if isinstance(detail, dict) else None
                error = f"HTTP {status}: {message or response.reason}"
                retryable = status in RETRYABLE_STATUS_CODES
                if not retryable and status not in {400, 404}:
                    raise _GenerationError(safe_error(error), current_model)
            except (requests.Timeout, requests.ConnectionError, ValueError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                retryable = True
            except _GenerationError:
                raise
            except Exception as exc:
                raise _GenerationError(safe_error(f"{type(exc).__name__}: {exc}"), current_model) from None
            error = safe_error(error)
            print(f"  ⚠️ Gemini ({provider}) {current_model} ({attempt}/{attempts}): {error}")
            if not retryable or attempt == attempts:
                break
            time.sleep(min(2 ** attempt, 8))
    raise _GenerationError(error, current_model)


def generate_json(prompt, model, attempts=3, image=None, required_field=None,
                  response_model=None, fallback_models=None, provider=None):
    resolved_provider = (provider or config.GEMINI_PROVIDER).strip().lower()
    parts = [{"text": prompt}]
    if image is not None:
        parts.insert(0, {"inlineData": image})

    def parse(content):
        result = json.loads(content)
        if not isinstance(result, dict) or "error" in result:
            raise ValueError("Gemini 응답은 분석 결과 JSON 객체여야 합니다")
        if required_field and not has_text(result, required_field):
            raise ValueError(f"Gemini 응답에 필수 텍스트 누락 ({required_field})")
        return response_model.model_validate(result).model_dump() if response_model else result

    try:
        result, current_model, version = _generate(
            resolved_provider, model, fallback_models, attempts, parts,
            {"responseMimeType": "application/json"}, parse,
        )
    except _GenerationError as exc:
        return {"error": str(exc), "_provider": resolved_provider, "_model": exc.model}

    result = {**result, "_provider": resolved_provider, "_model": current_model}
    if version:
        result["_model_version"] = version
    else:
        result.pop("_model_version", None)
    return result


def generate_text(prompt, model, attempts=2, fallback_models=None, provider=None) -> tuple[str, str]:
    resolved_provider = (provider or config.GEMINI_PROVIDER).strip().lower()
    content, current_model, version = _generate(
        resolved_provider, model, fallback_models, attempts, [{"text": prompt}], {}, str,
    )
    return content, version or current_model
