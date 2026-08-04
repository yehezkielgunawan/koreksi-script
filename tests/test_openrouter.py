import base64
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import grader_core.openrouter as openrouter
from grader_core.config import RubricConfig
from grader_core.grading import (
    GradingValidationError,
    grading_response_schema,
)
from grader_core.openrouter import (
    DEFAULT_MODEL,
    InvalidModelResponseError,
    OpenRouterClient,
)


def _response(content: str, *, request_id: str = "req-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=request_id,
        model=DEFAULT_MODEL,
        provider="test-provider",
        usage=SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=20,
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4 fake submission bytes"


class FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeAPIError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _fake_openai(outcomes: list[object]) -> tuple[SimpleNamespace, FakeCompletions]:
    completions = FakeCompletions(outcomes)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _write_pdf(tmp_path: Path, name: str = "answer.pdf") -> Path:
    pdf_path = tmp_path / name
    pdf_path.write_bytes(_pdf_bytes())
    return pdf_path


def test_default_sdk_configuration_has_bounded_timeout_and_no_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(openrouter, "OpenAI", FakeOpenAI)

    client = OpenRouterClient(api_key="test-key")

    assert client.request_timeout_seconds == 180.0
    assert client.max_attempts == 2
    assert captured == {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "test-key",
        "timeout": 180.0,
        "max_retries": 0,
    }


@pytest.mark.parametrize("timeout", [0, -1])
def test_request_timeout_must_be_positive(timeout: float) -> None:
    fake_client, _ = _fake_openai([])

    with pytest.raises(ValueError, match="request_timeout_seconds"):
        OpenRouterClient(client=fake_client, request_timeout_seconds=timeout)


def test_grade_request_sends_question_text_and_base64_pdf_file_part(
    tmp_path: Path,
) -> None:
    pdf_path = _write_pdf(tmp_path)
    fake_client, completions = _fake_openai([_response(json.dumps(_grading_payload()))])
    client = OpenRouterClient(client=fake_client)

    result = client.request_grade("Grading prompt", "Explain COBIT.", pdf_path)

    call = completions.calls[0]
    assert result.overall_feedback == "Belum ada data."
    assert call["model"] == DEFAULT_MODEL
    assert call["temperature"] == 0
    assert call["seed"] == 0
    assert call["extra_body"] == {"provider": {"require_parameters": True}}
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["response_format"]["json_schema"]["name"] == "grading_response"
    user_content = call["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert "Explain COBIT." in user_content[0]["text"]
    assert "QUESTION" in user_content[0]["text"]
    assert user_content[1]["type"] == "file"
    assert user_content[1]["file"]["filename"] == "answer.pdf"
    file_data = user_content[1]["file"]["file_data"]
    assert file_data.startswith("data:application/pdf;base64,")
    assert base64.b64decode(file_data.split(",", 1)[1]) == _pdf_bytes()
    assert client.last_metadata is not None
    assert client.last_metadata.request_id == "req-1"
    assert client.last_metadata.provider == "test-provider"
    assert client.last_metadata.total_tokens == 20


def test_grade_request_passes_response_schema(tmp_path: Path) -> None:
    pdf_path = _write_pdf(tmp_path)
    fake_client, completions = _fake_openai([_response(json.dumps(_grading_payload()))])
    client = OpenRouterClient(client=fake_client)
    schema = grading_response_schema(
        RubricConfig.model_validate(
            {
                "schema_version": 1,
                "assignment": {
                    "id": "test-assignment",
                    "title": "Test assignment",
                    "total_points": 10,
                    "feedback_language": "id",
                    "overall_feedback_below": 8,
                },
                "items": [
                    {
                        "id": "question_1",
                        "label": "Question 1",
                        "max_points": 10,
                        "criteria": [
                            {
                                "id": "criterion_a",
                                "description": "Explains the answer",
                                "max_points": 10,
                                "required_evidence": "A clear explanation",
                                "levels": [
                                    {"score": 0, "description": "Missing"},
                                    {"score": 10, "description": "Complete"},
                                ],
                            }
                        ],
                    }
                ],
            }
        )
    )

    result = client.request_grade(
        "Grading prompt", "Explain COBIT.", pdf_path, schema
    )

    assert result.overall_feedback == "Belum ada data."
    call = completions.calls[0]
    assert call["response_format"]["json_schema"]["schema"] == schema
    assert "Explain COBIT." in call["messages"][1]["content"][0]["text"]


def _grading_payload(criterion_id: str = "criterion_a") -> dict[str, object]:
    return {
        "assessments": [
            {
                "item_id": "question_1",
                "criterion_id": criterion_id,
                "selected_score": 0,
                "rationale": "Tidak ada bukti yang cukup.",
                "evidence": [],
            }
        ],
        "item_feedback": {"question_1": "Perlu ditinjau."},
        "overall_feedback": "Belum ada data.",
        "review_reasons": [],
    }


def test_grading_validation_failure_gets_correction_with_error_feedback(
    tmp_path: Path,
) -> None:
    def validator(response: object) -> None:
        if response.assessments[0].criterion_id != "criterion_a":
            raise GradingValidationError(
                "unknown assessment for question_1/criterion_x"
            )

    pdf_path = _write_pdf(tmp_path)
    fake_client, completions = _fake_openai(
        [
            _response(json.dumps(_grading_payload("criterion_x"))),
            _response(json.dumps(_grading_payload("criterion_a"))),
        ]
    )
    client = OpenRouterClient(client=fake_client, max_attempts=1)

    result = client.request_grade(
        "Grading prompt", "Explain COBIT.", pdf_path, validator=validator
    )

    assert result.assessments[0].criterion_id == "criterion_a"
    assert len(completions.calls) == 2
    correction = completions.calls[1]["messages"][-1]["content"]
    assert "unknown assessment for question_1/criterion_x" in correction


def test_grading_validation_failure_twice_raises_invalid_response(
    tmp_path: Path,
) -> None:
    def validator(_response: object) -> None:
        raise GradingValidationError("missing assessment for question_1/criterion_a")

    pdf_path = _write_pdf(tmp_path)
    fake_client, completions = _fake_openai(
        [
            _response(json.dumps(_grading_payload())),
            _response(json.dumps(_grading_payload())),
        ]
    )
    client = OpenRouterClient(client=fake_client, max_attempts=1)

    with pytest.raises(
        InvalidModelResponseError, match="remained invalid after one correction request"
    ):
        client.request_grade("Grading prompt", "Explain COBIT.", pdf_path, validator=validator)

    assert len(completions.calls) == 2


def test_correction_message_includes_parse_error_details(tmp_path: Path) -> None:
    pdf_path = _write_pdf(tmp_path)
    fake_client, completions = _fake_openai(
        [
            _response("not json"),
            _response(json.dumps(_grading_payload())),
        ]
    )
    client = OpenRouterClient(client=fake_client, max_attempts=1)

    result = client.request_grade("Grading prompt", "Explain COBIT.", pdf_path)

    assert result.assessments[0].criterion_id == "criterion_a"
    correction = completions.calls[1]["messages"][-1]["content"]
    assert "Expecting value" in correction


def test_transient_rate_limit_is_retried_with_backoff(tmp_path: Path) -> None:
    pdf_path = _write_pdf(tmp_path)
    fake_client, completions = _fake_openai(
        [
            FakeAPIError(429),
            _response(json.dumps(_grading_payload())),
        ]
    )
    sleeps: list[float] = []
    client = OpenRouterClient(
        client=fake_client,
        max_attempts=2,
        retry_base_seconds=0.25,
        sleep=sleeps.append,
    )

    result = client.request_grade("Grading prompt", "Explain COBIT.", pdf_path)

    assert result.assessments[0].criterion_id == "criterion_a"
    assert len(completions.calls) == 2
    assert sleeps == [0.25]


def test_timeout_is_retried_once_with_application_retry(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    pdf_path = _write_pdf(tmp_path)
    fake_client, completions = _fake_openai(
        [
            TimeoutError("request timed out"),
            _response(json.dumps(_grading_payload())),
        ]
    )
    sleeps: list[float] = []
    client = OpenRouterClient(client=fake_client, sleep=sleeps.append)

    result = client.request_grade("Grading prompt", "Explain COBIT.", pdf_path)

    assert result.assessments[0].criterion_id == "criterion_a"
    assert len(completions.calls) == 2
    assert sleeps == [2.0]
    assert "retrying in 2.0s" in capsys.readouterr().err


def test_invalid_json_gets_one_correction_request(tmp_path: Path) -> None:
    pdf_path = _write_pdf(tmp_path)
    fake_client, completions = _fake_openai(
        [
            _response("not json"),
            _response(json.dumps(_grading_payload())),
        ]
    )
    client = OpenRouterClient(client=fake_client, max_attempts=1)

    result = client.request_grade("Grading prompt", "Explain COBIT.", pdf_path)

    assert result.assessments[0].criterion_id == "criterion_a"
    assert len(completions.calls) == 2
    assert "valid JSON" in completions.calls[1]["messages"][-1]["content"]


def test_non_transient_error_is_not_retried(tmp_path: Path) -> None:
    pdf_path = _write_pdf(tmp_path)
    fake_client, completions = _fake_openai([FakeAPIError(401)])
    client = OpenRouterClient(client=fake_client, max_attempts=3)

    with pytest.raises(FakeAPIError):
        client.request_grade("Grading prompt", "Explain COBIT.", pdf_path)

    assert len(completions.calls) == 1
