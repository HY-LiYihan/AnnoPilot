from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LlmSettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 20.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or Path.cwd() / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_llm_settings() -> LlmSettings:
    load_dotenv()
    return LlmSettings(
        base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", "gpt5.5"),
        timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
    )
