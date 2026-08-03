# Koreksi Script

Auditable grading of student PDF, DOCX, and DOC submissions with a multimodal OpenRouter model. The unified `grader.py` CLI extracts text and visual evidence, validates structured model responses, calculates scores locally from YAML rubrics, and writes version-2 results with a separate human-review queue.

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
  <STUDENT_ID>_<STUDENT_NAME>/
    answer.pdf|answer.docx|answer.doc
    Question.html
```

Only one supported answer document is accepted per student. Files whose names contain `question`, `soal`, `pertanyaan`, `attachment`, `ai_usage`, `ai form`, or `declaration` are excluded from answer discovery. `Question.html` is optional, but its absence is reported for review.

## Rubrics

Rubrics are strict YAML files validated with Pydantic. The repository includes:

- `rubrics/individual.yaml` for 100-point individual essays with question weights `25/35/40`.
- `rubrics/group.yaml` for 100-point group-paper background review.

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
  --output results_v2.json \
  --dry-run
```

Run grading after the dry run succeeds:

```bash
uv run grader.py grade \
  --rubric rubrics/individual.yaml \
  --input /path/to/submissions \
  --output results_v2.json
```

Force a single student's submission to be graded again, bypassing the exact result cache:

```bash
uv run grader.py regrade \
  --student-id 2902737810 \
  --rubric rubrics/individual.yaml \
  --input /path/to/submissions \
  --output results_v2.json
```

The `--model`, `--visual-prompt`, and `--grading-prompt` options can override their defaults when testing a controlled configuration.

## Results and Review

The default result file is `results_v2.json`. Results use schema version 2 and contain a fingerprint covering the source document, normalized PDF, question, rubric, prompts, model, extractor, and schema version. Existing results are reused only when the complete fingerprint matches.

Each record has one of these statuses:

- `graded`: the response was valid and no review reason was raised.
- `needs_review`: grading completed with an ambiguity or evidence issue, or preprocessing could not produce a reliable document.
- `error`: processing failed and the error is persisted for investigation.

Review records are also written to `results_v2_review.json`. Review flags include unreadable evidence, missing or ambiguous answers, conversion failures, invalid evidence references, and insufficient text or visual evidence. Scores are never calculated by the model; the application calculates them after response validation.

Retired output filenames are rejected to prevent accidental mixing of incompatible schemas.

## Troubleshooting

- `OPENROUTER_API_KEY is required`: set the key in `.env` or the shell environment.
- `LibreOffice is required`: install LibreOffice before grading DOC or DOCX files; PDF-only dry runs do not require it.
- `no student submissions found`: check that the input contains directories beginning with `StudentAnswer` and student folders with supported documents.
- A `needs_review` record is intentional. Inspect the separate review queue instead of treating it as an automatic pass or failure.

Run the full verification suite with:

```bash
uv run pytest -q
uv run python -m compileall -q grader.py grader_core tests
```
