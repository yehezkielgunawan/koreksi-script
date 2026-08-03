# Retire Legacy Grader Paths

## Context

The repository now has a unified `grader.py` CLI backed by `grader_core`, versioned YAML rubrics, multimodal OpenRouter grading, deterministic score calculation, and version-2 result persistence. The repository still contains the earlier independent entry points, prompts, result snapshots, tests, and documentation. Those artifacts describe different APIs, models, schemas, and environment variables.

The final migration must leave one supported grading path and must not preserve stale student-result data in the repository.

## Goals

- Make `grader.py` the only supported grading entry point.
- Remove legacy scripts, prompt files, output snapshots, and tests that target deleted behavior.
- Document the current CLI, rubric format, environment requirements, conversion prerequisite, result files, and review workflow.
- Keep the migration explicit: legacy output filenames remain rejected instead of being silently overwritten.
- Verify the final repository with tests, compilation, rubric validation, and CLI smoke checks.

## Non-Goals

- Do not add compatibility wrappers for `main.py` or `group_review.py`.
- Do not migrate old result JSON into the version-2 schema.
- Do not change grading behavior, rubric scoring, model response schemas, or result persistence beyond migration-related documentation and ignore rules.
- Do not add a new API provider or a second CLI frontend.

## Design

### Single Supported Path

The supported flow is:

1. Install dependencies with `uv sync`.
2. Set `OPENROUTER_API_KEY` in the local `.env` file or environment.
3. Validate an individual or group rubric with `uv run grader.py validate`.
4. Run `uv run grader.py grade` against one `StudentAnswer*` directory or a parent containing those directories.
5. Use `--dry-run` before API calls and `regrade --student-id` for a forced individual regrade.

The CLI continues to use the configured Gemma model through OpenRouter, performs visual evidence extraction for selected pages, calculates totals locally, and writes version-2 results plus a separate review queue.

### Legacy Removal

The following tracked artifacts are removed:

- `main.py`
- `group_review.py`
- `Individual_Prompts.md`
- `Group_Prompts.md`
- `individual_results.json`
- `group_results.json`

The result snapshots are removed because they use obsolete schemas and contain student submission data. They are not migrated or archived in the repository. `.gitignore` will cover current version-2 result files, review queues, and the retired output names so local runs do not reintroduce them.

Tests that import or parse the deleted scripts are removed. Core extraction behavior remains covered by the `grader_core` document tests, and CLI behavior remains covered by the unified CLI tests.

### Documentation

`README.md` becomes the user-facing operating guide. It documents installation, credentials, command examples, input layout, YAML rubric selection, dry-run behavior, DOC/DOCX conversion, result files, review handling, and the free-endpoint warning.

`AGENTS.md` is updated to reflect the current architecture and commands. It no longer describes Gemini, the independent scripts, legacy prompts, dynamic prompt regex scoring, or old output files.

`REFACTORING_SUMMARY.md` is replaced with a concise current architecture and migration note so it does not continue to advertise removed files or schemas.

The package description in `pyproject.toml` is updated from the placeholder description to describe the unified student-assignment grader.

## Error Handling and Safety

- Missing `OPENROUTER_API_KEY`, invalid rubrics, missing input roots, unwritable output directories, missing prompts, and unavailable LibreOffice for DOC/DOCX grading remain preflight errors.
- Dry runs do not initialize the API client, make network requests, or write result files.
- Existing local legacy result files are not consumed by the version-2 store and legacy output names are rejected by the CLI.
- `.env`, student-answer directories, and generated results remain ignored by Git.

## Verification

The migration is complete only when all of the following pass:

- `uv run pytest -q`
- `uv run python -m compileall -q grader.py grader_core tests`
- `uv run python grader.py validate --rubric rubrics/individual.yaml`
- `uv run python grader.py validate --rubric rubrics/group.yaml`
- A temporary synthetic-submission dry run completes without an API client or result file.
- The final Git diff contains no references to removed legacy entry points, prompts, or output snapshots outside the migration history/specification.
