import json
from pathlib import Path

from backend.app.presets import APPRAISAL_ENGAGEMENT_TAG_SCHEMA, BUILTIN_SAMPLE_PRESETS
from backend.app.text_processing import split_sentences


SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "samples"


def test_appraisal_engagement_sample_files_match_builtin_presets() -> None:
    for preset in BUILTIN_SAMPLE_PRESETS.values():
        sample_path = SAMPLE_ROOT / preset.filename

        assert sample_path.exists(), f"Missing sample file for preset {preset.id}: {sample_path.name}"
        assert sample_path.read_text(encoding="utf-8") == preset.text


def test_appraisal_engagement_sample_sentences_are_unique() -> None:
    for preset in BUILTIN_SAMPLE_PRESETS.values():
        sentences = split_sentences(preset.text)

        assert len(sentences) >= 8, f"Preset {preset.id} should keep enough bilingual review material."
        assert len(sentences) == len(set(sentences)), f"Preset {preset.id} contains duplicated sentence text."


def test_appraisal_engagement_sample_schema_matches_builtin_schema() -> None:
    schema_path = SAMPLE_ROOT / "appraisal-engagement-tag-schema.json"
    disk_schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert disk_schema == APPRAISAL_ENGAGEMENT_TAG_SCHEMA
