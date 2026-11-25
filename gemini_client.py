"""Vertex AI GenerateContent 호출과 공통 JSON 응답 처리."""
import json
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
                   config.SUPABASE_KEY, config.TELEGRAM_BOT_TOKEN):
        if secret:
            message = message.replace(secret, "[redacted]")
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


def _response_text(body):
    candidates = body.get("candidates") or []
    if not candidates:
        reason = body.get("promptFeedback", {}).get("blockReason", "NO_CANDIDATES")
        raise ValueError(f"Gemini 응답 없음 ({reason})")
    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    if finish_reason and finish_reason != "STOP":
        raise ValueError(f"Gemini 응답 종료 ({finish_reason})")
    return "".join(
        part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
        if not part.get("thought")
    )


def generate_json(prompt, model, attempts=3, image=None, required_field=None, response_model=None):
    location = config.GOOGLE_CLOUD_LOCATION
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    project = quote(config.GOOGLE_CLOUD_PROJECT, safe="")
    model_path = quote(model, safe="")
    url = (f"https://{host}/v1/projects/{project}/locations/{location}"
           f"/publishers/google/models/{model_path}:generateContent")
    parts = [{"text": prompt}]
    if image is not None:
        parts.insert(0, {"inlineData": image})
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    metadata = {"_provider": "vertexai", "_model": model}
    for attempt in range(1, max(1, attempts) + 1):
        retryable = False
        try:
            response = _session().post(url, json=payload, timeout=(10, 90))
            if not response.ok:
                retryable = response.status_code in RETRYABLE_STATUS_CODES
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                detail = body.get("error", {}).get("message") if isinstance(body, dict) else ""
                error = f"HTTP {response.status_code}: {detail or response.reason}"
            else:
                body = response.json()
                result = json.loads(_response_text(body))
                if not isinstance(result, dict) or "error" in result:
                    raise ValueError("Gemini 응답은 분석 결과 JSON 객체여야 합니다")
                if required_field and not has_text(result, required_field):
                    raise ValueError(f"Gemini 응답에 필수 텍스트 누락 ({required_field})")
                if response_model:
                    result = response_model.model_validate(result).model_dump()
                reported_model = body.get("modelVersion")
                if reported_model:
                    metadata["_model_version"] = reported_model
                return {**result, **metadata}
        except (requests.Timeout, requests.ConnectionError) as exc:
            retryable = True
            error = f"{type(exc).__name__}: {safe_error(exc)}"
        except ValueError as exc:
            error = safe_error(exc)
            retryable = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {safe_error(exc)}"
        error = safe_error(error)
        print(f"  ⚠️ Vertex AI {model} ({attempt}/{max(1, attempts)}): {error}")
        if not retryable or attempt >= attempts:
            return {"error": error, **metadata}
        time.sleep(min(2 ** attempt, 15))
