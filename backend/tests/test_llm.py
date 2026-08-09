import io
import urllib.error

from backend.app.llm import OpenAICompatibleSuggestionReviewer
from backend.app.settings import LlmSettings


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
