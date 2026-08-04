# Koreksi Script

Auditable grading of student PDF, DOCX, and DOC submissions with a multimodal OpenRouter model. The unified `grader.py` CLI extracts text and visual evidence, validates structured model responses, calculates scores locally from YAML rubrics, and writes version-3 results with a separate human-review queue.

## Requirements

- Python 3.13
- [`uv`](https://docs.astral.sh/uv/)
- LibreOffice for DOC and DOCX conversion
- An OpenRouter API key

## Setup

```bash
uv sync
```

Create a local `.env` file, which is ignored by Git:

```dotenv
OPENROUTER_API_KEY=your_openrouter_api_key
```

The default model is `google/gemma-4-26b-a4b-it:free`. Pass `--model` to select another OpenRouter model. Free endpoints can be rate-limited and have provider privacy tradeoffs; the CLI prints a warning when one is selected.

## Input Layout

The input path must be one `StudentAnswer*` directory or a parent containing such directories:

```text
StudentAnswer_<COURSE>_<TYPE>_<ASSIGNMENT>_<DATE>/
  assignment.yaml
  Question.html
  <STUDENT_ID>_<STUDENT_NAME>/
    answer.pdf|answer.docx|answer.doc
    Question.html  # optional per-student override
```

Only one supported answer document is accepted per student. Files whose names contain `question`, `soal`, `pertanyaan`, `attachment`, `ai_usage`, `ai form`, or `declaration` are excluded from answer discovery. The grader uses the student-level `Question.html` first, then the assignment-root file. Missing or ambiguous question statements are sent to review.

## Rubrics

Rubrics are strict combined YAML catalogs validated with Pydantic. The repository includes:

- `rubrics/individual.yaml` for all individual assignment rubrics.
- `rubrics/group.yaml` for all group assignment rubrics.

Each `StudentAnswer*` directory may include an `assignment.yaml` selector. The selector chooses an assignment entry from the selected catalog; when the catalog contains exactly one assignment, the grader selects that sole entry if the selector is absent. A selector is required when the catalog contains multiple assignments. The catalog owns every question's points, criteria, required evidence, and score levels. A question may use a concise string criterion, which the loader expands into one deterministic full-point criterion with standard score levels and generic evidence validation. Detailed criterion lists remain supported when a question needs multiple weighted criteria.

Example shorthand question:

```yaml
id: question_1
label: Question 1
max_points: 30
criteria: Ability to explain the techniques and tools used.
```

Example selector:

```yaml
schema_version: 1
assignment:
  id: week-04
```

Validate a rubric before grading:

```bash
uv run grader.py validate --rubric rubrics/individual.yaml
uv run grader.py validate --rubric rubrics/group.yaml
```

## Commands

Preview discovery, document conversion, page rendering, and rubric selection without credentials or API requests:

```bash
uv run grader.py grade \
  --rubric rubrics/individual.yaml \
  --input /path/to/submissions \
  --output results_v3.json \
  --dry-run
```

Run grading after the dry run succeeds:

```bash
uv run grader.py grade \
  --rubric rubrics/individual.yaml \
  --input /path/to/submissions \
  --output results_v3.json
```

Force a single student's submission to be graded again, bypassing the exact result cache:

```bash
uv run grader.py regrade \
  --student-id 2902737810 \
  --rubric rubrics/individual.yaml \
  --input /path/to/submissions \
  --output results_v3.json
```

The `--model`, `--request-timeout`, `--visual-prompt`, and `--grading-prompt` options can override their defaults when testing a controlled configuration. Each model request has a 180-second timeout by default and is retried once for transient failures. Visual evidence requests send at most four page images at a time. Grading prints progress before and after each slow document or model operation, and continues to the next submission when a submission records an `error`.

## Results and Review

The default result file is `results_v3.json`. Results use schema version 3 and contain a fingerprint covering the source document, normalized PDF, question, selected catalog assignment, selector, prompts, model, extractor, and schema version. Existing results are reused only when the complete fingerprint matches.

Each record has one of these statuses:

- `graded`: the response was valid and no review reason was raised.
- `needs_review`: grading completed with an ambiguity or evidence issue, or preprocessing could not produce a reliable document.
- `error`: processing failed and the error is persisted for investigation.

Review records are also written to `results_v3_review.json`. Review flags include unreadable evidence, missing or ambiguous answers, conversion failures, invalid evidence references, and insufficient text or visual evidence. Scores are never calculated by the model; the application calculates them after response validation.

The final `total_score` is always an integer from `0` through `100`. Item percentages are also reported from `0` through `100`.

Retired output filenames are rejected to prevent accidental mixing of incompatible schemas.

## Troubleshooting

- `OPENROUTER_API_KEY is required`: set the key in `.env` or the shell environment.
- `LibreOffice is required`: install LibreOffice before grading DOC or DOCX files; PDF-only dry runs do not require it.
- `assignment selector not found`: add a strict `assignment.yaml` with an assignment ID to the `StudentAnswer*` root or affected student folder when the catalog contains multiple assignments.
- `no student submissions found`: check that the input contains directories beginning with `StudentAnswer` and student folders with supported documents.
- `request_timeout_seconds must be a finite positive number`: set `--request-timeout` to a number greater than zero; the default is 180 seconds.
- A `needs_review` record is intentional. Inspect the separate review queue instead of treating it as an automatic pass or failure.

Run the full verification suite with:

```bash
uv run pytest -q
uv run python -m compileall -q grader.py grader_core tests
```
