# Current Grader Architecture

The project uses one unified `grader.py` CLI backed by the `grader_core` package.

## Processing Flow

1. Validate a strict combined YAML rubric catalog.
2. Discover one supported answer document per student under `StudentAnswer*` directories and resolve its assignment selector.
3. Normalize DOC and DOCX files to PDF when LibreOffice is available, and count PDF pages.
4. Load the selected assignment's question-specific rubric and upload the submission PDF directly in one structured grading request to OpenRouter.
5. Validate all model evidence against the document and rubric.
6. Calculate criterion, item, and total scores in Python.
7. Persist fingerprinted version-4 results atomically and write a separate review queue.

## Current Files

- `grader.py`: CLI and orchestration.
- `grader_core/`: configuration, documents, grading, OpenRouter, and results modules.
- `prompts/`: grading prompt.
- `rubrics/`: combined individual and group catalogs with per-question scoring criteria; concise string criteria are normalized into explicit runtime criteria.
- `tests/`: core and CLI tests using synthetic documents and a fake API client.

## Migration Status

The former independent script workflow and its prompt/output snapshots have been retired. New runs must use a combined rubric catalog and `results_v4.json`; an `assignment.yaml` selector is optional for a single-assignment catalog and required when the catalog has multiple assignments. Old output filenames are rejected rather than overwritten. Scores and item percentages use a `0–100` scale. Each catalog assignment must total 100 points, and every question declares its own criteria, evidence requirements, and score levels after shorthand normalization.
