from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.storage import AnnotationStorage, ConflictError


def _schema() -> dict:
    return {
        "schema_version": "annopilot.tag_schema.v1",
        "record_type": "tag_schema",
        "tags": [
            {"id": "PER", "name": "Person", "shortcut": "1", "color": "#0b7565"},
            {"id": "ORG", "name": "Organization", "shortcut": "2", "color": "#326bd8"},
            {"id": "LOC", "name": "Location", "shortcut": "3", "color": "#c45a2e"},
        ],
    }


def _seed_assistance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sentence_count: int = 12):
    monkeypatch.setenv("ASSISTANCE_WORKER_ENABLED", "false")
    storage = AnnotationStorage(tmp_path / "runtime" / "annopilot.sqlite", tmp_path / "projects")
    client = TestClient(create_app(storage))
    client.__enter__()
    schema_response = client.post("/api/projects/default/tags/schema/import", json=_schema())
    assert schema_response.status_code == 200
    text = "\n".join(f"Alice visits Paris number {index}." for index in range(sentence_count))
    imported = client.post(
        "/api/projects/default/import-txt",
        files={"file": ("openner.txt", text.encode("utf-8"), "text/plain")},
    )
    assert imported.status_code == 200
    document_id = imported.json()["document_id"]
    document = client.get(f"/api/projects/default/documents/{document_id}").json()
    sentences = document["sentences"]
    for sentence in sentences[:5]:
        annotation = client.post(
            f"/api/projects/default/sentences/{sentence['id']}/annotations",
            json={"tag_id": "PER", "start_token_index": 0, "end_token_index": 0},
        )
        assert annotation.status_code == 200
        completed = client.post(
            f"/api/projects/default/sentences/{sentence['id']}/complete",
            json={"completed": True, "answer": "accept"},
        )
        assert completed.status_code == 200
    return client, storage, document_id, sentences


def _ready_one(storage: AnnotationStorage) -> tuple[str, dict]:
    job_id = storage.assistance_service.claim_jobs(1)[0]
    context = storage.assistance_service.get_generation_context(job_id)
    first = context["tokens"][0]
    storage.assistance_service.store_generation_result(
        job_id,
        {
            "candidate": {
                "text": context["source_text"],
                "spans": [
                    {
                        "start": first["start_char"],
                        "end": first["end_char"],
                        "text": first["text"],
                        "label": "PER",
                        "confidence": 0.95,
                    }
                ],
            },
            "model": "fake-openner-model",
            "prompt_sha256": "prompt-hash",
            "retrieved_examples": {"PER": ["Alice"]},
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        },
    )
    return job_id, context


def _ready_empty(storage: AnnotationStorage) -> tuple[str, dict]:
    job_id = storage.assistance_service.claim_jobs(1)[0]
    context = storage.assistance_service.get_generation_context(job_id)
    storage.assistance_service.store_generation_result(
        job_id,
        {
            "candidate": {"text": context["source_text"], "spans": []},
            "model": "fake-openner-model",
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        },
    )
    return job_id, context


def test_per_tag_seed_activates_five_job_window_and_confirm_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, storage, document_id, _sentences = _seed_assistance(tmp_path, monkeypatch)
    try:
        status = client.get(f"/api/projects/default/documents/{document_id}/assistance")
        assert status.status_code == 200
        payload = status.json()
        assert [tag["tag_id"] for tag in payload["active_tags"]] == ["PER"]
        assert payload["queue"]["queued"] == 5
        assert payload["tag_progress"][0]["human_verified_count"] == 5

        job_id, context = _ready_one(storage)
        ready = client.get(f"/api/projects/default/documents/{document_id}/assistance").json()
        draft = next(item for item in ready["queue"]["items"] if item["id"] == job_id)
        response = client.post(
            f"/api/projects/default/sentences/{context['sentence_id']}/assistance/decision",
            json={"action": "confirm", "draft_id": job_id, "draft_version": draft["draft_version"]},
        )
        assert response.status_code == 200
        assert response.json()["completed"] is True
        annotations = storage.get_sentence_annotations("default", context["sentence_id"])
        assert [(item["text"], item["tag_id"], item["source"]) for item in annotations] == [
            ("Alice", "PER", "accepted_suggestion")
        ]
        with storage.connect() as conn:
            feedback = conn.execute("SELECT action, reason_source FROM assistance_feedback WHERE job_id = ?", (job_id,)).fetchone()
            sentence = conn.execute("SELECT completed, answer FROM sentences WHERE id = ?", (context["sentence_id"],)).fetchone()
        assert tuple(feedback) == ("confirm", None)
        assert tuple(sentence) == (1, "accept")
        audit = client.get("/api/projects/default/audit").json()
        assert audit["non_replayable_event_count"] == 0
        assert audit["replay_issue_counts"] == {}
    finally:
        client.__exit__(None, None, None)


def test_concurrent_queue_refill_creates_one_job_per_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, storage, document_id, _sentences = _seed_assistance(tmp_path, monkeypatch)
    try:
        with storage.connect() as conn:
            conn.execute("DELETE FROM assistance_jobs WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM annotation_runs WHERE document_id = ? AND recipe = 'rag_llm_assistance'", (document_id,))

        barrier = threading.Barrier(8)
        errors: list[Exception] = []

        def refill() -> None:
            try:
                barrier.wait()
                storage.assistance_service.ensure_queue("default", document_id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=refill) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert errors == []
        assert all(not thread.is_alive() for thread in threads)
        with storage.connect() as conn:
            jobs = conn.execute(
                "SELECT sentence_id FROM assistance_jobs WHERE document_id = ?", (document_id,)
            ).fetchall()
            runs = conn.execute(
                "SELECT id FROM annotation_runs WHERE document_id = ? AND recipe = 'rag_llm_assistance'",
                (document_id,),
            ).fetchall()
        assert len(jobs) == 5
        assert len({row["sentence_id"] for row in jobs}) == 5
        assert len(runs) == 5
    finally:
        client.__exit__(None, None, None)


def test_skip_moves_draft_to_tail_without_deleting_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, storage, document_id, _sentences = _seed_assistance(tmp_path, monkeypatch, sentence_count=20)
    try:
        job_id, context = _ready_one(storage)
        before = client.get(f"/api/projects/default/documents/{document_id}/assistance").json()
        draft = next(item for item in before["queue"]["items"] if item["id"] == job_id)
        response = client.post(
            f"/api/projects/default/sentences/{context['sentence_id']}/assistance/decision",
            json={"action": "skip", "draft_id": job_id, "draft_version": 1},
        )
        assert response.status_code == 200
        after = client.get(f"/api/projects/default/documents/{document_id}/assistance").json()
        skipped = next(item for item in after["queue"]["items"] if item["id"] == job_id)
        assert skipped["status"] == "skipped"
        assert skipped["spans"] == draft["spans"]
        assert after["queue"]["queued"] == 5
    finally:
        client.__exit__(None, None, None)


def test_correct_saves_final_human_spans_and_queues_llm_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, storage, document_id, _sentences = _seed_assistance(tmp_path, monkeypatch)
    try:
        job_id, context = _ready_one(storage)
        paris = context["tokens"][2]
        response = client.post(
            f"/api/projects/default/sentences/{context['sentence_id']}/assistance/decision",
            json={
                "action": "correct",
                "draft_id": job_id,
                "draft_version": 1,
                "final_spans": [
                    {"tag_id": "LOC", "start_token_index": paris["token_index"], "end_token_index": paris["token_index"]}
                ],
            },
        )
        assert response.status_code == 200
        annotations = storage.get_sentence_annotations("default", context["sentence_id"])
        assert [(item["text"], item["tag_id"], item["source"]) for item in annotations] == [("Paris", "LOC", "human")]
        with storage.connect() as conn:
            feedback = conn.execute("SELECT action, reason_source FROM assistance_feedback WHERE job_id = ?", (job_id,)).fetchone()
        assert tuple(feedback) == ("correct", "pending")
    finally:
        client.__exit__(None, None, None)


def test_correct_requeues_following_unconfirmed_drafts_without_replacing_next_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, storage, document_id, _sentences = _seed_assistance(tmp_path, monkeypatch, sentence_count=20)
    try:
        first_job_id, first_context = _ready_one(storage)
        next_job_id, _next_context = _ready_one(storage)
        third_job_id, _third_context = _ready_one(storage)
        running_job_id = storage.assistance_service.claim_jobs(1)[0]
        running_context = storage.assistance_service.get_generation_context(running_job_id)
        paris = first_context["tokens"][2]

        response = client.post(
            f"/api/projects/default/sentences/{first_context['sentence_id']}/assistance/decision",
            json={
                "action": "correct",
                "draft_id": first_job_id,
                "draft_version": 1,
                "final_spans": [
                    {"tag_id": "LOC", "start_token_index": paris["token_index"], "end_token_index": paris["token_index"]}
                ],
            },
        )
        assert response.status_code == 200
        assert response.json()["superseded_count"] >= 2
        assert response.json()["requeued_count"] >= 2

        with storage.connect() as conn:
            next_job = conn.execute("SELECT status, draft_version FROM assistance_jobs WHERE id = ?", (next_job_id,)).fetchone()
            third_job = conn.execute("SELECT status, draft_version FROM assistance_jobs WHERE id = ?", (third_job_id,)).fetchone()
            running_job = conn.execute("SELECT status, draft_version FROM assistance_jobs WHERE id = ?", (running_job_id,)).fetchone()
            third_pending = conn.execute(
                "SELECT COUNT(*) FROM annotation_suggestions WHERE assistance_job_id = ? AND status = 'pending'",
                (third_job_id,),
            ).fetchone()[0]

        assert tuple(next_job) == ("ready", 1)
        assert tuple(third_job) == ("queued", 2)
        assert tuple(running_job) == ("queued", 2)
        assert third_pending == 0

        with pytest.raises(ConflictError):
            storage.assistance_service.store_generation_result(
                running_job_id,
                {
                    "candidate": {"text": running_context["source_text"], "spans": []},
                    "run_id": running_context["run_id"],
                    "draft_version": running_context["draft_version"],
                    "attempt_count": running_context["attempt_count"],
                },
            )
    finally:
        client.__exit__(None, None, None)


def test_assistance_accuracy_ewma_updates_from_confirm_and_correct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, storage, document_id, _sentences = _seed_assistance(tmp_path, monkeypatch, sentence_count=20)
    try:
        confirmed_job_id, confirmed_context = _ready_one(storage)
        confirm_response = client.post(
            f"/api/projects/default/sentences/{confirmed_context['sentence_id']}/assistance/decision",
            json={"action": "confirm", "draft_id": confirmed_job_id, "draft_version": 1},
        )
        assert confirm_response.status_code == 200

        corrected_job_id, corrected_context = _ready_one(storage)
        paris = corrected_context["tokens"][2]
        correct_response = client.post(
            f"/api/projects/default/sentences/{corrected_context['sentence_id']}/assistance/decision",
            json={
                "action": "correct",
                "draft_id": corrected_job_id,
                "draft_version": 1,
                "final_spans": [
                    {"tag_id": "LOC", "start_token_index": paris["token_index"], "end_token_index": paris["token_index"]}
                ],
            },
        )
        assert correct_response.status_code == 200

        metrics = client.get(f"/api/projects/default/documents/{document_id}/summary").json()["metrics"]
        assert metrics["assistance_accuracy_count"] == 2
        assert metrics["assistance_accuracy_ewma"] == pytest.approx(0.8)
        assert metrics["assistance_exact_match_rate"] == pytest.approx(0.5)
        assert metrics["assistance_correction_rate"] == pytest.approx(0.5)
    finally:
        client.__exit__(None, None, None)


def test_running_job_cannot_publish_over_human_annotation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, storage, _document_id, _sentences = _seed_assistance(tmp_path, monkeypatch)
    try:
        job_id = storage.assistance_service.claim_jobs(1)[0]
        context = storage.assistance_service.get_generation_context(job_id)
        storage.create_annotation("default", context["sentence_id"], "PER", 0, 0)
        with pytest.raises(ConflictError, match="manually annotated"):
            storage.assistance_service.store_generation_result(
                job_id,
                {"candidate": {"text": context["source_text"], "spans": []}, "model": "fake"},
            )
        with storage.connect() as conn:
            job = conn.execute("SELECT status FROM assistance_jobs WHERE id = ?", (job_id,)).fetchone()
        assert job["status"] == "cancelled"
        assert len(storage.get_sentence_annotations("default", context["sentence_id"])) == 1
    finally:
        client.__exit__(None, None, None)


def test_late_generation_result_cannot_overwrite_newer_lease_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, storage, _document_id, _sentences = _seed_assistance(tmp_path, monkeypatch)
    try:
        job_id = storage.assistance_service.claim_jobs(1)[0]
        first_context = storage.assistance_service.get_generation_context(job_id)
        with storage.connect() as conn:
            conn.execute(
                "UPDATE assistance_jobs SET lease_until = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", job_id),
            )
        assert storage.assistance_service.claim_jobs(1) == [job_id]

        with pytest.raises(ConflictError, match="attempt is stale"):
            storage.assistance_service.store_generation_result(
                job_id,
                {
                    "candidate": {"text": first_context["source_text"], "spans": []},
                    "attempt_count": first_context["attempt_count"],
                },
            )
        with storage.connect() as conn:
            row = conn.execute(
                "SELECT status, attempt_count FROM assistance_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        assert tuple(row) == ("running", 2)
    finally:
        client.__exit__(None, None, None)


def test_confirm_rolls_back_every_mutation_when_event_enqueue_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, storage, _document_id, _sentences = _seed_assistance(tmp_path, monkeypatch)
    try:
        job_id, context = _ready_one(storage)
        original_enqueue = storage.assistance_service.enqueue_event

        def fail_on_decision(conn, project_id, payload):
            if payload.get("type") == "assistance.draft.confirmed":
                raise RuntimeError("simulated outbox failure")
            return original_enqueue(conn, project_id, payload)

        storage.assistance_service.enqueue_event = fail_on_decision
        with pytest.raises(RuntimeError, match="simulated outbox failure"):
            client.post(
                f"/api/projects/default/sentences/{context['sentence_id']}/assistance/decision",
                json={"action": "confirm", "draft_id": job_id, "draft_version": 1},
            )

        with storage.connect() as conn:
            sentence = conn.execute("SELECT completed, answer FROM sentences WHERE id = ?", (context["sentence_id"],)).fetchone()
            job = conn.execute("SELECT status FROM assistance_jobs WHERE id = ?", (job_id,)).fetchone()
            suggestion = conn.execute(
                "SELECT status FROM annotation_suggestions WHERE assistance_job_id = ?",
                (job_id,),
            ).fetchone()
            feedback_count = conn.execute("SELECT COUNT(*) FROM assistance_feedback WHERE job_id = ?", (job_id,)).fetchone()[0]
        assert tuple(sentence) == (0, "pending")
        assert job["status"] == "ready"
        assert suggestion["status"] == "pending"
        assert feedback_count == 0
        assert storage.get_sentence_annotations("default", context["sentence_id"]) == []
    finally:
        client.__exit__(None, None, None)


def test_empty_draft_can_be_confirmed_as_trusted_negative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, storage, _document_id, _sentences = _seed_assistance(tmp_path, monkeypatch)
    try:
        job_id, context = _ready_empty(storage)
        response = client.post(
            f"/api/projects/default/sentences/{context['sentence_id']}/assistance/decision",
            json={"action": "confirm", "draft_id": job_id, "draft_version": 1},
        )
        assert response.status_code == 200
        assert response.json()["completed"] is True
        assert storage.get_sentence_annotations("default", context["sentence_id"]) == []
        with storage.connect() as conn:
            feedback = conn.execute(
                "SELECT action, original_spans_json, final_spans_json FROM assistance_feedback WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        assert tuple(feedback) == ("confirm", "[]", "[]")
    finally:
        client.__exit__(None, None, None)


def test_stale_draft_version_returns_conflict_without_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, storage, _document_id, _sentences = _seed_assistance(tmp_path, monkeypatch)
    try:
        job_id, context = _ready_one(storage)
        response = client.post(
            f"/api/projects/default/sentences/{context['sentence_id']}/assistance/decision",
            json={"action": "confirm", "draft_id": job_id, "draft_version": 99},
        )
        assert response.status_code == 409
        assert "stale" in response.json()["detail"].lower()
        assert storage.get_sentence_annotations("default", context["sentence_id"]) == []
        with storage.connect() as conn:
            job = conn.execute("SELECT status FROM assistance_jobs WHERE id = ?", (job_id,)).fetchone()
        assert job["status"] == "ready"
    finally:
        client.__exit__(None, None, None)


def test_prodigy_export_does_not_include_unconfirmed_assistance_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, storage, document_id, _sentences = _seed_assistance(tmp_path, monkeypatch)
    try:
        _job_id, context = _ready_one(storage)
        response = client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl")
        assert response.status_code == 200
        records = [json.loads(line) for line in response.text.splitlines()]
        draft_record = next(record for record in records if record["meta"]["sentence_id"] == context["sentence_id"])
        assert draft_record["spans"] == []
        assert draft_record["meta"]["suggestion_count"] == 1
    finally:
        client.__exit__(None, None, None)
