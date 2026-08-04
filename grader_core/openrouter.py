import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from grader_core.documents import RenderedPage
from grader_core.grading import (
    EvidencePackage,
    GradingResponse,
    GradingValidationError,
    VisualEvidenceResponse,
    grading_response_schema,
    visual_evidence_schema,
)


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_BASE_SECONDS = 2.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 180.0


class InvalidModelResponseError(RuntimeError):
    """Raised when the model response is not valid for the requested schema."""


@dataclass(frozen=True)
class RequestMetadata:
    request_id: str | None
    model: str | None
    provider: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    started_at: str
    completed_at: str


class OpenRouterClient:
    """Small, injectable OpenRouter client for structured multimodal requests."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must be non-negative")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")

        if client is None:
            resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
            if not resolved_api_key:
                raise ValueError("OPENROUTER_API_KEY is required")
            client = OpenAI(
                base_url=OPENROUTER_BASE_URL,
                api_key=resolved_api_key,
                timeout=request_timeout_seconds,
                max_retries=0,
            )

        self._client = client
        self.model = model
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self._sleep = sleep
        self.last_metadata: RequestMetadata | None = None

    def extract_visual_evidence(
        self,
        prompt: str,
        rendered_pages: Sequence[RenderedPage],
    ) -> VisualEvidenceResponse:
        content: list[dict[str, Any]] = [
            {
                    "type": "text",
                    "text": (
                        "Inspect every supplied page image and return visual evidence "
                        "only. Each page number is provided immediately before its image."
                ),
            }
        ]
        for rendered_page in rendered_pages:
            encoded = base64.b64encode(rendered_page.path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "text",
                    "text": f"Page {rendered_page.page_number} image follows.",
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{encoded}",
                        "detail": "high",
                    },
                }
            )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ]
        return self._request_and_parse(
            messages,
            VisualEvidenceResponse,
            visual_evidence_schema(),
            "visual_evidence_response",
        )

    def request_grade(
        self,
        prompt: str,
        evidence_package: EvidencePackage,
        response_schema: dict[str, Any] | None = None,
        validator: Callable[[GradingResponse], None] | None = None,
    ) -> GradingResponse:
        evidence_json = json.dumps(
            evidence_package.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    "Grade the submission using only this evidence package.\n\n"
                    f"{evidence_json}"
                ),
            },
        ]
        return self._request_and_parse(
            messages,
            GradingResponse,
            response_schema or grading_response_schema(),
            "grading_response",
            validator=validator,
        )

    def _request_and_parse(
        self,
        messages: list[dict[str, Any]],
        response_model: type[VisualEvidenceResponse] | type[GradingResponse],
        response_schema: dict[str, Any],
        schema_name: str,
        validator: Callable[[object], None] | None = None,
    ) -> VisualEvidenceResponse | GradingResponse:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": response_schema,
            },
        }
        response = self._create_completion(messages, response_format)
        try:
            return self._parse_and_validate(response, response_model, validator)
        except (InvalidModelResponseError, GradingValidationError) as first_error:
            correction_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Your previous response was invalid: "
                        f"{first_error}. Return only valid JSON matching the "
                        "supplied schema. Do not add Markdown or commentary."
                    ),
                },
            ]
            try:
                corrected_response = self._create_completion(
                    correction_messages, response_format
                )
                return self._parse_and_validate(
                    corrected_response, response_model, validator
                )
            except (
                InvalidModelResponseError,
                ValidationError,
                json.JSONDecodeError,
                GradingValidationError,
            ) as second_error:
                raise InvalidModelResponseError(
                    "Model response remained invalid after one correction request"
                ) from second_error

    def _create_completion(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any],
    ) -> Any:
        for attempt in range(self.max_attempts):
            started_at = _timestamp()
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format=response_format,
                    extra_body={"provider": {"require_parameters": True}},
                    temperature=0,
                    seed=0,
                )
                self.last_metadata = _request_metadata(response, started_at)
                return response
            except Exception as exc:
                if not _is_retryable(exc) or attempt == self.max_attempts - 1:
                    raise
                delay = self.retry_base_seconds * (2**attempt)
                print(
                    f"OpenRouter request failed on attempt {attempt + 1}/"
                    f"{self.max_attempts} ({type(exc).__name__}: {exc}); "
                    f"retrying in {delay:.1f}s.",
                    file=sys.stderr,
                    flush=True,
                )
                self._sleep(delay)

        raise RuntimeError("unreachable retry state")

    @staticmethod
    def _parse_response(
        response: Any,
        response_model: type[VisualEvidenceResponse] | type[GradingResponse],
    ) -> VisualEvidenceResponse | GradingResponse:
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise InvalidModelResponseError("Response did not contain message content") from exc
        if not isinstance(content, str) or not content.strip():
            raise InvalidModelResponseError("Response content was empty")
        try:
            payload = json.loads(content)
            return response_model.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise InvalidModelResponseError(
                f"Response was not valid structured JSON: {exc}"
            ) from exc

    @staticmethod
    def _parse_and_validate(
        response: Any,
        response_model: type[VisualEvidenceResponse] | type[GradingResponse],
        validator: Callable[[object], None] | None,
    ) -> VisualEvidenceResponse | GradingResponse:
        parsed = OpenRouterClient._parse_response(response, response_model)
        if validator is not None:
            validator(parsed)
        return parsed


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_metadata(response: Any, started_at: str) -> RequestMetadata:
    usage = getattr(response, "usage", None)
    return RequestMetadata(
        request_id=_optional_string(getattr(response, "id", None)),
        model=_optional_string(getattr(response, "model", None)),
        provider=_optional_string(getattr(response, "provider", None)),
        prompt_tokens=_usage_int(usage, "prompt_tokens"),
        completion_tokens=_usage_int(usage, "completion_tokens"),
        total_tokens=_usage_int(usage, "total_tokens"),
        started_at=started_at,
        completed_at=_timestamp(),
    )


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _usage_int(usage: Any, field: str) -> int | None:
    if usage is None:
        return None
    value = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
    return int(value) if value is not None else None


def _is_retryable(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
    if status_code == 429 or isinstance(status_code, int) and 500 <= status_code < 600:
        return True
    message = str(error).casefold()
    return any(term in message for term in ("rate limit", "timed out", "timeout", "temporarily unavailable"))
