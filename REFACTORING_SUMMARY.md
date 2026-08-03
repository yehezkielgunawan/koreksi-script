# Current Grader Architecture

The project uses one unified `grader.py` CLI backed by the `grader_core` package.

## Processing Flow

1. Validate a strict combined YAML rubric catalog.
2. Discover one supported answer document per student under `StudentAnswer*` directories and resolve its assignment selector.
3. Normalize DOC and DOCX files to PDF when LibreOffice is available.
4. Extract page text and diagnostics, then render only pages requiring visual evidence.
5. Load the selected assignment's question-specific rubric and request structured visual evidence and grading responses from OpenRouter.
6. Validate all model evidence against the document and rubric.
7. Calculate criterion, item, and total scores in Python.
8. Persist fingerprinted version-3 results atomically and write a separate review queue.

## Current Files

- `grader.py`: CLI and orchestration.
- `grader_core/`: configuration, documents, grading, OpenRouter, and results modules.
- `prompts/`: visual evidence and grading prompts.
- `rubrics/`: combined individual and group catalogs with explicit per-question scoring criteria.
- `tests/`: core and CLI tests using synthetic documents and a fake API client.

## Migration Status

The former independent script workflow and its prompt/output snapshots have been retired. New runs must use a combined rubric catalog, an `assignment.yaml` selector, and `results_v3.json`; old output filenames are rejected rather than overwritten. Scores and item percentages use a `0–100` scale. Each catalog assignment must total 100 points, and every question declares its own criteria, evidence requirements, and score levels.
