from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LlmSettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 20.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


@dataclass(frozen=True)
class RetrievalSettings:
    mode: str = "bm25"
    bm25_top_k: int = 30
    dense_top_k: int = 30
    rrf_k: int = 60
    dense_enabled: bool = False
    dense_base_url: str = ""
    dense_api_key: str = ""
    dense_model: str = "Qwen3-Embedding-0.6B"
    dense_timeout_seconds: float = 5.0

    @property
    def configured(self) -> bool:
        return bool(self.dense_enabled and self.dense_base_url and self.dense_model)


@dataclass(frozen=True)
class LlmModelOption:
    id: str
    family: str
    tier: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "family": self.family, "tier": self.tier, "model": self.model}


LLM_MODEL_OPTIONS = (
    LlmModelOption(id="gpt5.5-low", family="gpt5.5", tier="low", model="gpt-5.5"),
    LlmModelOption(id="gpt5.5-medium", family="gpt5.5", tier="medium", model="gpt-5.5"),
    LlmModelOption(id="gpt5.5-high", family="gpt5.5", tier="high", model="gpt-5.5"),
    LlmModelOption(id="gpt5.6-low", family="gpt5.6", tier="low", model="gpt-5.6-luna"),
    LlmModelOption(id="gpt5.6-medium", family="gpt5.6", tier="medium", model="gpt-5.6-sol"),
    LlmModelOption(id="gpt5.6-high", family="gpt5.6", tier="high", model="gpt-5.6-terra"),
)

LLM_MODEL_OPTION_ALIASES = {
    "gpt5.5": "gpt5.5-medium",
    "gpt-5.5": "gpt5.5-medium",
    "gpt5.6": "gpt5.6-medium",
    "gpt-5.6-luna": "gpt5.6-low",
    "gpt-5.6-sol": "gpt5.6-medium",
    "gpt-5.6-terra": "gpt5.6-high",
}

LLM_MODEL_OPTION_BY_ID = {option.id: option for option in LLM_MODEL_OPTIONS}


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


def list_llm_model_options() -> list[dict[str, Any]]:
    return [option.to_dict() for option in LLM_MODEL_OPTIONS]


def get_llm_model_option(option_id: str) -> LlmModelOption | None:
    normalized_id = LLM_MODEL_OPTION_ALIASES.get(option_id, option_id)
    return LLM_MODEL_OPTION_BY_ID.get(normalized_id)


def selected_llm_model_option_id(model: str) -> str | None:
    alias = LLM_MODEL_OPTION_ALIASES.get(model)
    if alias is not None:
        return alias
    for option in LLM_MODEL_OPTIONS:
        if option.model == model:
            return option.id
    return None


def get_llm_settings(model_override: str | None = None) -> LlmSettings:
    load_dotenv()
    return LlmSettings(
        base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=model_override or os.getenv("LLM_MODEL", "gpt-5.5"),
        timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
    )


def get_retrieval_settings() -> RetrievalSettings:
    load_dotenv()
    mode = os.getenv("ASSISTANCE_RETRIEVAL_MODE", "bm25").strip().lower()
    return RetrievalSettings(
        mode=mode if mode in {"bm25", "hybrid"} else "bm25",
        bm25_top_k=int(os.getenv("BM25_TOP_K", "30")),
        dense_top_k=int(os.getenv("DENSE_TOP_K", "30")),
        rrf_k=int(os.getenv("RRF_K", "60")),
        dense_enabled=os.getenv("DENSE_RETRIEVAL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        dense_base_url=os.getenv("DENSE_RETRIEVAL_BASE_URL", "").rstrip("/"),
        dense_api_key=os.getenv("DENSE_RETRIEVAL_API_KEY", ""),
        dense_model=os.getenv("DENSE_RETRIEVAL_MODEL", "Qwen3-Embedding-0.6B"),
        dense_timeout_seconds=float(os.getenv("DENSE_RETRIEVAL_TIMEOUT_SECONDS", "5")),
    )
