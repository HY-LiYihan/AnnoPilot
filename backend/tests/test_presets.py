import json
from pathlib import Path

from backend.app.presets import APPRAISAL_ENGAGEMENT_TAG_SCHEMA, BUILTIN_SAMPLE_PRESETS, OPENNER_TAG_SCHEMA
from backend.app.text_processing import split_sentences


SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "samples"


def test_appraisal_engagement_sample_files_match_builtin_presets() -> None:
    for preset_id, preset in BUILTIN_SAMPLE_PRESETS.items():
        if preset_id == "openner-zh-en-1000":
            continue
        sample_path = SAMPLE_ROOT / preset.filename

        assert sample_path.exists(), f"Missing sample file for preset {preset.id}: {sample_path.name}"
        assert sample_path.read_text(encoding="utf-8") == preset.text


def test_appraisal_engagement_sample_sentences_are_unique() -> None:
    for preset_id, preset in BUILTIN_SAMPLE_PRESETS.items():
        if preset_id == "openner-zh-en-1000":
            continue
        sentences = split_sentences(preset.text)

        assert len(sentences) >= 8, f"Preset {preset.id} should keep enough bilingual review material."
        assert len(sentences) == len(set(sentences)), f"Preset {preset.id} contains duplicated sentence text."


def test_appraisal_engagement_sample_schema_matches_builtin_schema() -> None:
    schema_path = SAMPLE_ROOT / "appraisal-engagement-tag-schema.json"
    disk_schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert disk_schema == APPRAISAL_ENGAGEMENT_TAG_SCHEMA


def test_appraisal_engagement_schema_encodes_theoretical_hierarchy() -> None:
    taxonomies = {tag["id"]: tag["taxonomy"] for tag in APPRAISAL_ENGAGEMENT_TAG_SCHEMA["tags"]}

    assert taxonomies["engagement_monogloss"] == {
        "framework": "appraisal",
        "system": "engagement",
        "dialogic_status": "monogloss",
        "orientation": None,
        "family": "monogloss",
        "subtype": None,
        "path": ["engagement", "monogloss"],
        "default_scope": "proposition",
    }
    heterogloss = [taxonomy for taxonomy in taxonomies.values() if taxonomy["dialogic_status"] == "heterogloss"]
    assert len(heterogloss) == 8
    assert sum(taxonomy["orientation"] == "expansion" for taxonomy in heterogloss) == 3
    assert sum(taxonomy["orientation"] == "contraction" for taxonomy in heterogloss) == 5
    assert {taxonomy["family"] for taxonomy in heterogloss} == {"entertain", "attribute", "proclaim", "disclaim"}
    assert all(taxonomy["path"][:2] == ["engagement", "heterogloss"] for taxonomy in heterogloss)
    assert all(taxonomy["default_scope"] == "cue" for taxonomy in heterogloss)


def test_openner_sample_has_one_thousand_sentences_per_language() -> None:
    chinese = (SAMPLE_ROOT / "openner_chinese_1000.txt").read_text(encoding="utf-8").splitlines()
    english = (SAMPLE_ROOT / "openner_english_1000.txt").read_text(encoding="utf-8").splitlines()
    preset = BUILTIN_SAMPLE_PRESETS["openner-zh-en-1000"]

    assert len(chinese) == 1000
    assert len(english) == 1000
    assert preset.tag_schema == OPENNER_TAG_SCHEMA
    assert preset.clear_tags_on_load is True
