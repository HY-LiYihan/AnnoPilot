# AnnoPilot

Local-first agentic annotation workbench for turning concepts into auditable datasets.

AnnoPilot helps researchers and data builders move from a rough concept definition to a traceable annotation workflow. It is designed for low-resource, evolving, or domain-specific annotation tasks where the hard part is not only labeling examples, but also refining the definition, reviewing uncertain cases, and keeping the process reproducible.

## What It Is

AnnoPilot is the next home for the Rosetta annotation workflow. The project focuses on:

- concept-driven annotation setup from short guidelines and small gold sets
- prompt training and definition refinement before large batch runs
- LLM-assisted batch annotation with review routing
- human-in-the-loop correction for hard or low-confidence examples
- local-first runtime storage, traces, reports, and exports
- Prodigy-compatible JSONL as a practical interchange format

The goal is not to replace expert judgment. The goal is to make expert judgment travel farther: into reusable guidelines, calibrated prompts, review queues, and auditable dataset artifacts.

## Why AnnoPilot

Traditional annotation tools are excellent at collecting labels once a task is already well specified. AnnoPilot is built for the earlier and messier stage:

```text
concept definition
-> gold examples
-> prompt calibration
-> batch annotation
-> review and correction
-> exportable dataset
```

This makes it useful for linguistics, digital humanities, NLP research, and domain datasets where the label boundary changes as the researcher learns.

## Status

This repository is being prepared as the public project home for AnnoPilot.

The original implementation is being migrated from the Rosetta codebase. Until the migration is complete, this repository may contain project notes, design documents, and release planning before the full application code lands.

## Planned Scope

- Streamlit-based local application
- project overview and task setup
- guideline and gold-example management
- prompt validation and optimization
- LLM provider configuration
- embedding-based example retrieval
- batch annotation queue
- review queue for uncertain or inconsistent outputs
- manual span and relation annotation
- JSONL export, reports, and run manifests
- Docker deployment path

## License

MIT license is planned for this project.

