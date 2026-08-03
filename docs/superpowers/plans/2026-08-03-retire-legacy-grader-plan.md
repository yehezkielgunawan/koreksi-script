# Retire Legacy Grader Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the obsolete independent grader workflow and leave a documented, verified `grader.py` CLI as the repository's only supported entry point.

**Architecture:** Keep the existing unified pipeline unchanged: `grader.py` orchestrates discovery, normalization, visual evidence extraction, deterministic scoring, and version-2 persistence through `grader_core`. This migration only removes stale entry points and artifacts, updates repository guidance, and verifies the existing behavior.

**Tech Stack:** Python 3.13, `uv`, `pytest`, Pydantic, PyYAML, OpenRouter-compatible `openai` client, `pypdf`, `pdfplumber`, `pypdfium2`, Markdown documentation.

---

## File Map

| Path | Responsibility in this migration |
|---|---|
| `main.py` | Delete obsolete individual-assignment implementation. |
| `group_review.py` | Delete obsolete Gemini group-assignment implementation. |
| `Individual_Prompts.md` | Delete prompt source superseded by `prompts/` and `rubrics/`. |
| `Group_Prompts.md` | Delete prompt source superseded by `prompts/` and `rubrics/`. |
| `individual_results.json` | Delete stale legacy-schema student-result snapshot. |
| `group_results.json` | Delete stale legacy-schema student-result snapshot. |
| `tests/test_pdf_extraction.py` | Delete tests that import removed scripts. |
| `tests/test_pypdf_migration.py` | Delete tests that parse removed scripts. |
| `.gitignore` | Ignore current version-2 outputs, review queues, and retired output names. |
| `README.md` | Replace outdated setup and usage instructions with the unified CLI guide. |
| `AGENTS.md` | Replace the old architecture and operational gotchas with current project guidance. |
| `REFACTORING_SUMMARY.md` | Replace the stale historical summary with a current architecture and migration note. |
| `pyproject.toml` | Replace the placeholder package description. |
| `grader.py` | Preserve the existing explicit rejection of retired output filenames. |
| `tests/test_cli.py` | Preserve coverage that retired output filenames are rejected. |
| `docs/superpowers/specs/2026-08-03-retire-legacy-grader-design.md` | Approved migration design. |

## Task 1: Remove Legacy Artifacts

**Files:**
- Delete: `main.py`
- Delete: `group_review.py`
- Delete: `Individual_Prompts.md`
- Delete: `Group_Prompts.md`
- Delete: `individual_results.json`
- Delete: `group_results.json`
- Delete: `tests/test_pdf_extraction.py`
- Delete: `tests/test_pypdf_migration.py`
- Modify: `.gitignore`

- [ ] **Step 1: Confirm the exact tracked legacy set**

Run:

```bash
git ls-files main.py group_review.py Individual_Prompts.md Group_Prompts.md individual_results.json group_results.json tests/test_pdf_extraction.py tests/test_pypdf_migration.py
```

Expected: the eight paths listed above, one per line.

- [ ] **Step 2: Add output protections to `.gitignore`**

Append this block after the existing results/log section in `.gitignore`:

```gitignore
# Versioned grader outputs are local artifacts
results_v2.json
*_review.json
individual_results.json
group_results.json
```

This prevents generated result files and retired names from becoming tracked again while leaving source rubrics and prompts visible to Git.

- [ ] **Step 3: Delete the obsolete files**

Run:

```bash
git rm main.py group_review.py Individual_Prompts.md Group_Prompts.md individual_results.json group_results.json tests/test_pdf_extraction.py tests/test_pypdf_migration.py
```

Do not replace the removed scripts with wrappers. `grader.py` remains the sole entry point.

- [ ] **Step 4: Inspect the staged deletion set**

Run:

```bash
git status --short
git diff --stat -- .gitignore main.py group_review.py Individual_Prompts.md Group_Prompts.md individual_results.json group_results.json tests/test_pdf_extraction.py tests/test_pypdf_migration.py
git diff --check
```

Expected: eight deletions, one `.gitignore` modification, and no whitespace errors.

- [ ] **Step 5: Commit the legacy removal**

```bash
git commit -m "Remove legacy grader workflow"
```

## Task 2: Rewrite the User README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the obsolete README content**

Replace `README.md` with this complete guide:

```markdown
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

Retired output names such as `individual_results.json` and `group_results.json` are rejected to prevent accidental mixing of incompatible schemas.

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
```

- [ ] **Step 2: Check README for removed-provider and legacy instructions**

Run:

```bash
git grep -n -E 'Gemini|GOOGLE_API_KEY|main\.py|group_review\.py|Individual_Prompts\.md|Group_Prompts\.md' -- README.md
```

Expected: no output and exit status 1.

- [ ] **Step 3: Commit the README migration**

```bash
git add README.md
git commit -m "Document unified grader CLI"
```

## Task 3: Update Repository Guidance and Metadata

**Files:**
- Modify: `AGENTS.md`
- Modify: `REFACTORING_SUMMARY.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Replace `AGENTS.md` with current project guidance**

Use this complete content:

```markdown
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
| `grader_core/results.py` | Fingerprinted version-2 results, atomic writes, cache, and review queue |
| `grader.py` | CLI preflight and end-to-end orchestration |
| `rubrics/` | Individual and group YAML scoring criteria |
| `prompts/` | Visual evidence and grading system prompts |

## Commands

```bash
uv sync
uv run grader.py validate --rubric rubrics/individual.yaml
uv run grader.py validate --rubric rubrics/group.yaml
uv run grader.py grade --rubric rubrics/individual.yaml --input /path/to/submissions --output results_v2.json --dry-run
uv run grader.py grade --rubric rubrics/individual.yaml --input /path/to/submissions --output results_v2.json
uv run grader.py regrade --student-id <ID> --rubric rubrics/individual.yaml --input /path/to/submissions --output results_v2.json
```

Dry runs perform discovery, rubric validation, document conversion, and page rendering without initializing the API client or writing results. `regrade` bypasses the exact fingerprint cache for one student.

## Environment

- Python 3.13, managed with `uv`.
- `.env` requires `OPENROUTER_API_KEY` for `grade` and `regrade`.
- `.env` is ignored and must never be committed.
- LibreOffice is required when grading DOC or DOCX files.
- The default model is `google/gemma-4-26b-a4b-it:free`; the CLI accepts `--model`.

## Key Behavior

- Rubrics are strict YAML and must have matching criterion, item, and assignment totals.
- The model returns evidence and criterion assessments; Python calculates totals and selects the weakest item.
- Results are saved atomically after each submission and skipped only when the complete fingerprint matches.
- `needs_review` records are persisted separately in `results_v2_review.json`.
- Missing, ambiguous, unreadable, or failed-conversion submissions are not silently graded.
- Only `StudentAnswer*` directories are scanned. Known question, attachment, declaration, and AI-usage filenames are excluded from answer discovery.
- Retired output names are rejected to prevent mixing old and version-2 schemas.

## Testing

```bash
uv run pytest -q
uv run python -m compileall -q grader.py grader_core tests
```

There is no live-API test in the suite. CLI integration tests use a local fake client, and dry-run tests verify that no API client is initialized.
```

- [ ] **Step 2: Replace `REFACTORING_SUMMARY.md` with a current migration note**

Use this content:

```markdown
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
```

- [ ] **Step 3: Update the package description**

Change the `description` field in `pyproject.toml` from:

```toml
description = "Add your description here"
```

to:

```toml
description = "Auditable multimodal grading of student assignments"
```

- [ ] **Step 4: Verify repository guidance contains no stale instructions**

Run:

```bash
git grep -n -E 'Gemini|GOOGLE_API_KEY|google-generativeai|main\.py|group_review\.py|Individual_Prompts\.md|Group_Prompts\.md|individual_results\.json|group_results\.json' -- AGENTS.md README.md REFACTORING_SUMMARY.md pyproject.toml
```

Expected: no output and exit status 1.

- [ ] **Step 5: Commit the guidance update**

```bash
git add AGENTS.md REFACTORING_SUMMARY.md pyproject.toml
git commit -m "Update project guidance for unified grader"
```

## Task 4: Run Final Verification

**Files:**
- Verify: `grader.py`, `grader_core/`, `prompts/`, `rubrics/`, `tests/`
- Verify: `.gitignore`, `README.md`, `AGENTS.md`, `REFACTORING_SUMMARY.md`, `pyproject.toml`

- [ ] **Step 1: Run the complete test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass with zero failures. Removing the four legacy-script test cases should leave 86 passing tests from the current 90-test baseline.

- [ ] **Step 2: Compile the maintained Python sources**

Run:

```bash
uv run python -m compileall -q grader.py grader_core tests
```

Expected: no output and exit status 0.

- [ ] **Step 3: Validate both checked-in rubrics**

Run:

```bash
uv run python grader.py validate --rubric rubrics/individual.yaml
uv run python grader.py validate --rubric rubrics/group.yaml
```

Expected output includes:

```text
Rubric valid: individual-essay (3 items, 100 points)
Rubric valid: group-paper-background (1 items, 100 points)
```

- [ ] **Step 4: Run the no-network CLI smoke test**

Run:

```bash
uv run pytest -q tests/test_cli.py::test_grade_dry_run_discovers_and_normalizes_without_api_client
```

Expected: one passing test, no result file created by the test, and no API client initialization.

- [ ] **Step 5: Confirm removed files and intentional guards**

Run:

```bash
test ! -e main.py
test ! -e group_review.py
test ! -e Individual_Prompts.md
test ! -e Group_Prompts.md
test ! -e individual_results.json
test ! -e group_results.json
git ls-files main.py group_review.py Individual_Prompts.md Group_Prompts.md individual_results.json group_results.json
```

Expected: the `test` commands succeed and `git ls-files` prints no paths.

Then verify only the intentional output-name guards remain:

```bash
git grep -n -E 'individual_results\.json|group_results\.json' -- grader.py tests/test_cli.py .gitignore
```

Expected: matches only in the explicit rejection test, `LEGACY_OUTPUT_NAMES`, or ignore rules.

- [ ] **Step 6: Inspect the final repository state**

Run:

```bash
git diff --check
git status --short
git log --oneline -5
```

Expected: no whitespace errors, no unintended worktree changes, and the migration commits visible in the recent history.
