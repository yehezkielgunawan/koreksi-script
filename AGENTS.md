# Koreksi Script - Student Assignment Checker

Automated, auditable scoring of student PDF, DOCX, and DOC assignments using an OpenRouter model that reads submission PDFs directly.

## Architecture

`grader.py` is the only supported entry point. It orchestrates these modules:

| Module | Responsibility |
|---|---|
| `grader_core/config.py` | Strict YAML rubric models and validation |
| `grader_core/documents.py` | Submission discovery, HTML extraction, normalization, diagnostics, and page counting |
| `grader_core/grading.py` | Structured response schemas, evidence validation, and deterministic score calculation |
| `grader_core/openrouter.py` | OpenRouter structured requests with direct PDF upload |
| `grader_core/results.py` | Fingerprinted version-4 results, atomic writes, cache, and review queue |
| `grader.py` | CLI preflight and end-to-end orchestration |
| `rubrics/` | Individual and group YAML scoring criteria |
| `prompts/` | Grading system prompt |

## Commands

```bash
uv sync
uv run grader.py validate --rubric rubrics/individual.yaml
uv run grader.py validate --rubric rubrics/group.yaml
uv run grader.py grade --rubric rubrics/individual.yaml --input /path/to/submissions --output results_v4.json --dry-run
uv run grader.py grade --rubric rubrics/individual.yaml --input /path/to/submissions --output results_v4.json
uv run grader.py regrade --student-id <ID> --rubric rubrics/individual.yaml --input /path/to/submissions --output results_v4.json
```

Dry runs perform discovery, assignment-selector and catalog validation, document conversion, question mapping, and page counting without initializing the API client or writing results. `regrade` bypasses the exact fingerprint cache for one student.

## Environment

- Python 3.13, managed with `uv`.
- `.env` requires `OPENROUTER_API_KEY` for `grade` and `regrade`.
- `.env` is ignored and must never be committed.
- LibreOffice is required when grading DOC or DOCX files.
- The default model is `google/gemma-4-26b-a4b-it:free`; the CLI accepts `--model`.

## Key Behavior

- Rubrics are strict combined YAML catalogs; each selected assignment defines every question's points, criteria, required evidence, and score levels. A string criterion is expanded into one deterministic full-point criterion; detailed criterion lists remain supported.
- Each `StudentAnswer*` root may provide an `assignment.yaml` selector whose ID exists in the selected catalog. If the catalog has exactly one assignment, that assignment is selected when the selector is absent; multiple assignments require an explicit selector.
- Question statements come from a student-level `Question.html` or the assignment-root fallback. Ambiguous mappings require review.
- The submission PDF is uploaded directly to the model in one request; the model returns criterion assessments with page citations, and Python validates evidence and calculates totals and the weakest item.
- Results are saved atomically after each submission and skipped only when the complete fingerprint matches.
- `needs_review` records are persisted separately in `results_v4_review.json`.
- `total_score` and item percentages use a `0-100` scale.
- Missing, ambiguous, unreadable, or failed-conversion submissions are not silently graded.
- Only `StudentAnswer*` directories are scanned. Known question, attachment, declaration, and AI-usage filenames are excluded from answer discovery.
- Retired output names (`individual_results.json`, `group_results.json`, `results_v3.json`) are rejected to prevent mixing old and version-4 schemas.

## Testing

```bash
uv run pytest -q
uv run python -m compileall -q grader.py grader_core tests
```

There is no live-API test in the suite. CLI integration tests use a local fake client, and dry-run tests verify that no API client is initialized.
