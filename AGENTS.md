# Koreksi Script - Student Assignment Checker

Automated, auditable scoring of student PDF, DOCX, and DOC assignments using a multimodal OpenRouter model.

## Architecture

`grader.py` is the only supported entry point. It orchestrates these modules:

| Module | Responsibility |
|---|---|
| `grader_core/config.py` | Strict YAML rubric models and validation |
| `grader_core/documents.py` | Submission discovery, HTML extraction, normalization, diagnostics, and page rendering |
| `grader_core/grading.py` | Structured response schemas, evidence validation, and deterministic score calculation |
| `grader_core/openrouter.py` | OpenRouter structured text and multimodal requests |
| `grader_core/results.py` | Fingerprinted version-3 results, atomic writes, cache, and review queue |
| `grader.py` | CLI preflight and end-to-end orchestration |
| `rubrics/` | Individual and group YAML scoring criteria |
| `prompts/` | Visual evidence and grading system prompts |

## Commands

```bash
uv sync
uv run grader.py validate --rubric rubrics/individual.yaml
uv run grader.py validate --rubric rubrics/group.yaml
uv run grader.py grade --rubric rubrics/individual.yaml --input /path/to/submissions --output results_v3.json --dry-run
uv run grader.py grade --rubric rubrics/individual.yaml --input /path/to/submissions --output results_v3.json
uv run grader.py regrade --student-id <ID> --rubric rubrics/individual.yaml --input /path/to/submissions --output results_v3.json
```

Dry runs perform discovery, assignment-selector and catalog validation, document conversion, question mapping, and page rendering without initializing the API client or writing results. `regrade` bypasses the exact fingerprint cache for one student.

## Environment

- Python 3.13, managed with `uv`.
- `.env` requires `OPENROUTER_API_KEY` for `grade` and `regrade`.
- `.env` is ignored and must never be committed.
- LibreOffice is required when grading DOC or DOCX files.
- The default model is `google/gemma-4-26b-a4b-it:free`; the CLI accepts `--model`.

## Key Behavior

- Rubrics are strict combined YAML catalogs; each selected assignment defines every question's points, criteria, required evidence, and score levels.
- Each `StudentAnswer*` root requires an `assignment.yaml` selector whose ID exists in the selected catalog.
- Question statements come from a student-level `Question.html` or the assignment-root fallback. Ambiguous mappings require review.
- The model returns evidence and criterion assessments; Python calculates totals and selects the weakest item.
- Results are saved atomically after each submission and skipped only when the complete fingerprint matches.
- `needs_review` records are persisted separately in `results_v3_review.json`.
- `total_score` and item percentages use a `0-100` scale.
- Missing, ambiguous, unreadable, or failed-conversion submissions are not silently graded.
- Only `StudentAnswer*` directories are scanned. Known question, attachment, declaration, and AI-usage filenames are excluded from answer discovery.
- Retired output names are rejected to prevent mixing old and version-3 schemas.

## Testing

```bash
uv run pytest -q
uv run python -m compileall -q grader.py grader_core tests
```

There is no live-API test in the suite. CLI integration tests use a local fake client, and dry-run tests verify that no API client is initialized.
