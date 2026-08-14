from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.storage import AnnotationStorage


class FakeEngagementGenerator:
    model = "fake-engagement-1"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.temperatures: list[float] = []

    def generate(self, prompt: str, temperature: float) -> str:
        self.prompts.append(prompt)
        self.temperatures.append(temperature)
        return self.responses.pop(0)


def make_storage(tmp_path: Path) -> AnnotationStorage:
    return AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )


def candidate(text: str, start: int, end: int, label: str) -> str:
    return json.dumps(
        {
            "text": text,
            "spans": [{"start": start, "end": end, "text": text[start:end], "label": label, "confidence": 0.96}],
            "explanation": "The cue opens space for another position.",
        },
        ensure_ascii=False,
    )


def test_same_prompt_k_engagement_candidates_persist_gate_and_export_prodigy(tmp_path: Path) -> None:
    source = "It may change."
    fake = FakeEngagementGenerator([])
    storage = make_storage(tmp_path)
    with TestClient(create_app(storage, engagement_candidate_generator=fake)) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("engagement-en.txt", source, "text/plain")},
        ).json()
        document_id = imported["document_id"]
        tag = client.post(
            "/api/projects/default/tags",
            json={"name": "Entertain", "description": "Opens dialogic space.", "examples": ["may"]},
        ).json()["tag"]
        sentence = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=1").json()["sentences"][0]
        label = tag["id"]
        fake.responses = [candidate(source, 3, 6, label) for _ in range(3)]

        response = client.post(
            f"/api/projects/default/documents/{document_id}/engagement/candidates/run",
            json={"candidate_count": 3, "temperature": 0.8, "sentence_id": sentence["id"]},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["candidate_count"] == 3
        assert len(payload["groups"]) == 3
        assert {group["verifier_status"] for group in payload["groups"]} == {"passed"}
        assert {group["consistency"]["route"] for group in payload["groups"]} == {"high"}
        assert all(group["consistency"]["auto_accept_eligible"] for group in payload["groups"])
        assert len(payload["suggestions"]) == 3
        assert len(set(fake.prompts)) == 1
        assert fake.temperatures == [0.8, 0.8, 0.8]

        sentence_after = client.get(f"/api/projects/default/documents/{document_id}").json()["sentences"][0]
        assert {item["consistency_route"] for item in sentence_after["suggestions"]} == {"high"}
        auto_accept = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/auto-accept",
            json={"min_confidence": 0.9, "complete_sentences": False},
        )
        assert auto_accept.status_code == 200
        assert auto_accept.json()["accepted"] == 1
        assert auto_accept.json()["skipped"] == 2

        export = client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl")
        assert export.status_code == 200
        records = [json.loads(line) for line in export.text.splitlines()]
        assert records[0]["text"] == source
        assert records[0]["spans"][0]["start"] == 3
        assert records[0]["spans"][0]["end"] == 6
        assert records[0]["spans"][0]["label"] == "Entertain"

        candidate_runs = [
            json.loads(line)
            for line in client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.candidate-runs.jsonl").text.splitlines()
        ]
        assert len(candidate_runs) == 3
        assert {item["meta"]["candidate_index"] for item in candidate_runs} == {0, 1, 2}
        assert {item["meta"]["verifier_status"] for item in candidate_runs} == {"passed"}
        assert {item["meta"]["candidate_consistency"]["route"] for item in candidate_runs} == {"high"}


def test_invalid_or_disagreeing_candidate_never_enters_auto_accept_path(tmp_path: Path) -> None:
    source = "It may change."
    storage = make_storage(tmp_path)
    fake = FakeEngagementGenerator([])
    with TestClient(create_app(storage, engagement_candidate_generator=fake)) as client:
        document_id = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("engagement-low.txt", source, "text/plain")},
        ).json()["document_id"]
        tag = client.post(
            "/api/projects/default/tags",
            json={"name": "Entertain", "description": "Opens dialogic space.", "examples": ["may"]},
        ).json()["tag"]
        sentence = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=1").json()["sentences"][0]
        fake.responses = [
            candidate(source, 3, 6, tag["id"]),
            candidate(source, 7, 13, tag["id"]),
            "not json",
        ]

        response = client.post(
            f"/api/projects/default/documents/{document_id}/engagement/candidates/run",
            json={"candidate_count": 3, "sentence_id": sentence["id"]},
        )
        assert response.status_code == 200
        groups = response.json()["groups"]
        assert [group["verifier_status"] for group in groups] == ["passed", "passed", "failed"]
        assert {group["consistency"]["route"] for group in groups} == {"low"}
        assert all(group["consistency"]["auto_accept_eligible"] is False for group in groups)

        auto_accept = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/auto-accept",
            json={"min_confidence": 0.9},
        )
        assert auto_accept.status_code == 200
        assert auto_accept.json()["accepted"] == 0
        assert auto_accept.json()["skipped"] == 2
        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert document["metrics"]["annotation_count"] == 0
        candidate_runs = [
            json.loads(line)
            for line in client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.candidate-runs.jsonl").text.splitlines()
        ]
        assert len(candidate_runs) == 3
        failed = next(item for item in candidate_runs if item["meta"]["verifier_status"] == "failed")
        assert failed["spans"] == []
        assert failed["meta"]["candidate_consistency"]["auto_accept_eligible"] is False
