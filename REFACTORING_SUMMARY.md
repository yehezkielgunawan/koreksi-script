# Current Grader Architecture

The project uses one unified `grader.py` CLI backed by the `grader_core` package.

## Processing Flow

1. Validate a strict YAML rubric.
2. Discover one supported answer document per student under `StudentAnswer*` directories.
3. Normalize DOC and DOCX files to PDF when LibreOffice is available.
4. Extract page text and diagnostics, then render only pages requiring visual evidence.
5. Request structured visual evidence and grading responses from OpenRouter.
6. Validate all model evidence against the document and rubric.
7. Calculate criterion, item, and total scores in Python.
8. Persist fingerprinted version-2 results atomically and write a separate review queue.

## Current Files

- `grader.py`: CLI and orchestration.
- `grader_core/`: configuration, documents, grading, OpenRouter, and results modules.
- `prompts/`: visual evidence and grading prompts.
- `rubrics/`: individual and group scoring criteria.
- `tests/`: core and CLI tests using synthetic documents and a fake API client.

## Migration Status

The former independent script workflow and its prompt/output snapshots have been retired. New runs must use the unified CLI and `results_v2.json`; old output filenames are rejected rather than overwritten.
