import io
import urllib.error

from backend.app.llm import OpenAICompatibleSuggestionReviewer, normalize_review_payload
from backend.app.settings import LlmSettings


def test_normalize_review_payload_preserves_rosetta_judge_fields() -> None:
    review = normalize_review_payload(
        """
        {
          "recommendation": "accept",
          "confidence": 0.93,
          "rationale": "边界合理。",
          "judge": {
            "format_score": 1,
            "concept_fit_score": 0.91,
            "boundary_score": 0.82,
            "relation_score": 1,
            "missed_span_risk": 0.1,
            "extra_span_risk": 0.2,
            "overall_score": 0.88,
            "needs_review": false,
            "error_types": ["wrong_label", "unknown"],
            "risk_flags": ["borderline_concept", "unknown"]
          }
        }
        """,
        model="gpt5.5-low",
    )

    assert review["model"] == "gpt5.5-low"
    assert review["recommendation"] == "accept"
    assert review["judge"]["boundary_score"] == 0.82
    assert review["judge"]["error_types"] == ["wrong_label"]
    assert review["judge"]["risk_flags"] == ["borderline_concept"]


def test_http_error_message_includes_provider_body_and_redacts_key() -> None:
    settings = LlmSettings(base_url="https://api.example.test/v1", api_key="test-api-key", model="gpt5.5")
    reviewer = OpenAICompatibleSuggestionReviewer(settings)
    error = urllib.error.HTTPError(
        url="https://api.example.test/v1/chat/completions",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(b'{"error":{"message":"model not supported for test-api-key"}}'),
    )

    message = reviewer._format_http_error(error)

    assert "HTTP 400 Bad Request" in message
    assert "model not supported" in message
    assert "test-api-key" not in message
    assert "[redacted]" in message
