from __future__ import annotations

import json
from collections import Counter
from typing import Any


class GoldsmithBootstrapReportService:
    """Human-readable bootstrap report based on exported Goldsmith artifacts."""

    def __init__(self, schema_version: str) -> None:
        self.schema_version = schema_version

    def build_lines(
        self,
        *,
        project_id: str,
        document_id: str,
        generated_at: str,
        document: dict[str, Any],
        prodigy_readiness: dict[str, Any],
        goldsmith_review_queue_lines: list[str],
        goldsmith_consistency_score_lines: list[str],
        goldsmith_label_statistics_lines: list[str],
        goldsmith_reflection_plan_lines: list[str],
        goldsmith_review_task_lines: list[str],
        goldsmith_verification_report_lines: list[str],
    ) -> list[str]:
        metrics = document.get("metrics") or {}
        doc_meta = document.get("document") or {}
        review_queue = self._payloads(goldsmith_review_queue_lines)
        consistency_scores = self._payloads(goldsmith_consistency_score_lines)
        label_stats = self._payloads(goldsmith_label_statistics_lines)
        reflection_plans = self._payloads(goldsmith_reflection_plan_lines)
        review_tasks = self._payloads(goldsmith_review_task_lines)
        verification_report = (self._payloads(goldsmith_verification_report_lines) or [{}])[0]

        route_counts = Counter(str(item.get("rosetta_route") or item.get("route") or "none") for item in consistency_scores)
        reflection_counts = Counter(
            str(item.get("item_type") or "unknown")
            for plan in reflection_plans
            for item in plan.get("items", [])
        )
        top_tokens = sorted(
            label_stats,
            key=lambda item: (
                int(item.get("entity_count") or 0),
                float(item.get("entity_probability") or 0.0),
                str(item.get("token") or ""),
            ),
            reverse=True,
        )[:10]

        lines = [
            f"<!-- schema_version: {self.schema_version} -->",
            "# Goldsmith Bootstrap Report",
            "",
            "## Summary",
            "",
            f"- Project: `{project_id}`",
            f"- Document: `{document_id}`",
            f"- Filename: `{doc_meta.get('filename', '')}`",
            f"- Generated at: `{generated_at}`",
            f"- Sentences: {metrics.get('sentence_count', 0)}",
            f"- Completed: {metrics.get('completed_count', 0)} / {metrics.get('sentence_count', 0)}",
            f"- Progress: {float(metrics.get('progress') or 0.0) * 100:.2f}%",
            f"- Accepted spans: {metrics.get('annotation_count', 0)}",
            f"- Pending suggestions: {self._pending_suggestion_count(metrics)}",
            f"- Prodigy readiness: `{prodigy_readiness.get('status', 'unknown')}`",
            "",
            "## Review Routes",
            "",
            "| route | count |",
            "| --- | ---: |",
        ]
        for route in ("high", "medium", "low", "none"):
            if route == "none" and not route_counts.get(route):
                continue
            lines.append(f"| {route} | {route_counts.get(route, 0)} |")

        lines.extend(
            [
                "",
                "## Human Review Queue",
                "",
                f"- Queue size: {len(review_queue)}",
                f"- Review tasks: {len(review_tasks)}",
                "- Policy: prioritize low/medium route items; sample high-confidence items for audit.",
                "",
            ]
        )
        if review_queue:
            lines.extend(["| rank | sentence | route | risk | cue |", "| ---: | ---: | --- | ---: | --- |"])
            for item in review_queue[:8]:
                suggestion = item.get("first_suggestion") or {}
                cue = suggestion.get("text") or item.get("action_hint") or ""
                lines.append(
                    "| {rank} | {sentence} | {route} | {risk:.2f} | {cue} |".format(
                        rank=item.get("rank", ""),
                        sentence=int(item.get("sentence_index") or 0) + 1,
                        route=item.get("review_route") or item.get("rosetta_route") or "",
                        risk=float(item.get("risk_score") or 0.0),
                        cue=self._table_cell(str(cue)),
                    )
                )
        else:
            lines.append("No pending review queue items.")

        lines.extend(
            [
                "",
                "## Top Entity Tokens",
                "",
                "| token | entity_count | entity_prob | context_prob | other_prob |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        if top_tokens:
            for item in top_tokens:
                lines.append(
                    "| {token} | {entity_count} | {entity_probability:.2f} | {context_probability:.2f} | {other_probability:.2f} |".format(
                        token=self._table_cell(str(item.get("token") or "")),
                        entity_count=int(item.get("entity_count") or 0),
                        entity_probability=float(item.get("entity_probability") or 0.0),
                        context_probability=float(item.get("context_probability") or 0.0),
                        other_probability=float(item.get("other_probability") or 0.0),
                    )
                )
        else:
            lines.append("| _none_ | 0 | 0.00 | 0.00 | 0.00 |")

        lines.extend(["", "## Reflection Items", "", "| type | count |", "| --- | ---: |"])
        if reflection_counts:
            for item_type, count in sorted(reflection_counts.items()):
                lines.append(f"| {self._table_cell(item_type)} | {count} |")
        else:
            lines.append("| _none_ | 0 |")

        verification_summary = verification_report.get("summary") or {}
        lines.extend(
            [
                "",
                "## Verification",
                "",
                f"- Status: `{verification_summary.get('status', 'unknown')}`",
                f"- Issues: {verification_summary.get('issue_count', 0)}",
                f"- Errors: {verification_summary.get('error_count', 0)}",
                f"- Warnings: {verification_summary.get('warning_count', 0)}",
                "",
                "## Recommended Actions",
                "",
            ]
        )
        lines.extend(self._recommended_actions(metrics, review_queue, reflection_counts, verification_summary, prodigy_readiness))
        lines.append("")
        return [line + "\n" for line in lines]

    @staticmethod
    def _payloads(lines: list[str]) -> list[dict[str, Any]]:
        payloads = []
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    @staticmethod
    def _pending_suggestion_count(metrics: dict[str, Any]) -> int:
        status_counts = metrics.get("suggestion_status_counts") or {}
        if status_counts.get("pending") is not None:
            return int(status_counts.get("pending") or 0)
        return int(metrics.get("suggestion_count") or 0)

    @staticmethod
    def _recommended_actions(
        metrics: dict[str, Any],
        review_queue: list[dict[str, Any]],
        reflection_counts: Counter[str],
        verification_summary: dict[str, Any],
        prodigy_readiness: dict[str, Any],
    ) -> list[str]:
        actions: list[str] = []
        pending = GoldsmithBootstrapReportService._pending_suggestion_count(metrics)
        if verification_summary.get("status") in {"error", "warning"}:
            actions.append("- Resolve verification issues before treating the bundle as final gold data.")
        if pending:
            actions.append("- Start with the hybrid Goldsmith review queue and clear pending suggestions before final export.")
        if review_queue:
            actions.append("- Review the first 5-8 queue items first; they carry the highest current disagreement or risk signal.")
        if reflection_counts:
            actions.append("- Inspect reflection items for missed spans and boundary drift before accepting high-volume lexical seeds.")
        if prodigy_readiness.get("status") != "ready":
            blockers = ", ".join(prodigy_readiness.get("blockers") or []) or "readiness blockers"
            actions.append(f"- Clear Prodigy readiness blockers: {blockers}.")
        if not actions:
            actions.append("- Export is ready; keep this report with the Prodigy bundle for audit and calibration notes.")
        return actions

    @staticmethod
    def _table_cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")
