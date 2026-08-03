import base64
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from grader_core.documents import RenderedPage
from grader_core.grading import (
    EvidencePackage,
    grading_response_schema,
)
from grader_core.openrouter import DEFAULT_MODEL, OpenRouterClient


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


def test_visual_request_sends_text_before_base64_images_and_schema(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "page-0001.png"
    image_path.write_bytes(b"fake-png")
    fake_client, completions = _fake_openai(
        [
            _response(
                json.dumps(
                    {
                        "evidence": [
                            {
                                "page": 1,
                                "transcription": "Diagram label",
                                "description": "A simple diagram.",
                                "readability": "clear",
                            }
                        ],
                        "review_reasons": [],
                    }
                )
            )
        ]
    )
    client = OpenRouterClient(client=fake_client)

    result = client.extract_visual_evidence(
        "Visual prompt",
        (RenderedPage(page_number=1, path=image_path, width=10, height=10),),
    )

    call = completions.calls[0]
    assert result.evidence[0].page == 1
    assert call["model"] == DEFAULT_MODEL
    assert call["temperature"] == 0
    assert call["seed"] == 0
    assert call["extra_body"] == {"provider": {"require_parameters": True}}
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    user_content = call["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert user_content[1]["text"] == "Page 1 image follows."
    assert user_content[2]["type"] == "image_url"
    assert user_content[2]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert base64.b64decode(user_content[2]["image_url"]["url"].split(",", 1)[1]) == b"fake-png"
    assert client.last_metadata is not None
    assert client.last_metadata.request_id == "req-1"
    assert client.last_metadata.provider == "test-provider"
    assert client.last_metadata.total_tokens == 20


def test_grade_request_sends_serialized_evidence_and_returns_response() -> None:
    fake_client, completions = _fake_openai(
        [
            _response(
                json.dumps(
                    {
                        "assessments": [
                            {
                                "item_id": "question_1",
                                "criterion_id": "criterion_a",
                                "selected_score": 0,
                                "rationale": "Tidak ada bukti yang cukup.",
                                "evidence": [],
                                "readability": "clear",
                            }
                        ],
                        "item_feedback": {"question_1": "Perlu ditinjau."},
                        "overall_feedback": "Belum ada data.",
                        "review_reasons": [],
                    }
                )
            )
        ]
    )
    client = OpenRouterClient(client=fake_client)
    package = EvidencePackage(
        question_text="Explain COBIT.",
        answer_text="The answer.",
    )

    result = client.request_grade("Grading prompt", package, grading_response_schema())

    assert result.overall_feedback == "Belum ada data."
    call = completions.calls[0]
    assert call["response_format"]["json_schema"]["name"] == "grading_response"
    assert call["response_format"]["json_schema"]["schema"] == grading_response_schema()
    user_text = call["messages"][1]["content"]
    assert "Explain COBIT." in user_text
    assert "The answer." in user_text


def test_transient_rate_limit_is_retried_with_backoff() -> None:
    fake_client, completions = _fake_openai(
        [
            FakeAPIError(429),
            _response(
                json.dumps(
                    {
                        "evidence": [
                            {
                                "page": 1,
                                "transcription": "Text",
                                "description": "Description",
                                "readability": "clear",
                            }
                        ]
                    }
                )
            ),
        ]
    )
    sleeps: list[float] = []
    client = OpenRouterClient(
        client=fake_client,
        max_attempts=2,
        retry_base_seconds=0.25,
        sleep=sleeps.append,
    )

    result = client.extract_visual_evidence("Prompt", ())

    assert result.evidence[0].page == 1
    assert len(completions.calls) == 2
    assert sleeps == [0.25]


def test_invalid_json_gets_one_correction_request() -> None:
    fake_client, completions = _fake_openai(
        [
            _response("not json"),
            _response(
                json.dumps(
                    {
                        "evidence": [
                            {
                                "page": 1,
                                "transcription": "Corrected",
                                "description": "Corrected description",
                                "readability": "clear",
                            }
                        ]
                    }
                )
            ),
        ]
    )
    client = OpenRouterClient(client=fake_client, max_attempts=1)

    result = client.extract_visual_evidence("Prompt", ())

    assert result.evidence[0].transcription == "Corrected"
    assert len(completions.calls) == 2
    assert "valid JSON" in completions.calls[1]["messages"][-1]["content"]


def test_non_transient_error_is_not_retried() -> None:
    fake_client, completions = _fake_openai([FakeAPIError(401)])
    client = OpenRouterClient(client=fake_client, max_attempts=3)

    with pytest.raises(FakeAPIError):
        client.extract_visual_evidence("Prompt", ())

    assert len(completions.calls) == 1
