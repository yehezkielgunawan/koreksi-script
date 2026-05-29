# Koreksi Script — Student Assignment Checker

Automated scoring of student assignments (PDF/DOCX) using LLM APIs.

## Architecture

Two independent scripts, each with its own API and prompt file:

| Script | Purpose | API | Prompt File | Output |
|---|---|---|---|---|
| `main.py` | Individual assignments | OpenRouter (OpenAI-compatible, model: `nvidia/nemotron-3-super-120b-a12b:free`) | `Individual_Prompts.md` | `individual_results.json` |
| `group_review.py` | Group assignments | Google Gemini (`gemini-2.5-flash`) | `Group_Prompts.md` | `group_results.json` |

## Commands

```bash
# Install dependencies
uv sync

# Run individual checker
uv run main.py                # normal
uv run main.py --debug        # debug logging + raw API responses in output
uv run main.py --re-review    # re-grade students scoring below 80

# Run group checker
uv run group_review.py
```

No tests, linter, or CI exist. There is no build step.

## Environment

- **Python 3.13** (see `.python-version`), managed with **`uv`**
- `.env` requires: `OPENROUTER_API_KEY` and `GOOGLE_API_KEY`
- **Never commit `.env`** — it contains live API keys and is in `.gitignore`

## Key Gotchas

- **`group_review.py` depends on `google-generativeai`** which is NOT in `pyproject.toml` or `requirements.txt`. Install manually: `uv pip install google-generativeai`
- **Scoring config is parsed dynamically** from the prompt file via regex (`Question N: [score]/MAX`). Changing prompt format without updating regex patterns in `parse_scoring_config_from_prompt()` will silently break scoring.
- **Prompt extraction** starts from the first line beginning with `"This is my student"` in the prompt `.md` file. Content above that line is ignored.
- **`.doc` extraction** uses macOS `textutil` — will fail on non-macOS without fallback.
- **Free OpenRouter models** (`:free` suffix) are rate-limited to 20 RPM; the script enforces a 3s delay between requests. Paid models skip the local throttle.
- **Results are saved incrementally** after each student — safe to interrupt and resume; already-scored students are skipped.
- **File discovery**: only scans `StudentAnswer*` directories. Files with keywords `question`, `soal`, `pertanyaan`, `attachment`, `ai_usage`, `ai form`, `declaration` in the filename are skipped.

## Using MCPs with This Project

- **Context7**: Use for looking up `openai` Python SDK, `PyPDF2`, `python-docx`, `google-generativeai`, or `pydantic` API docs when modifying API calls or parsing logic.
- **Serena**: Configured for Python LSP. Use `serena_find_symbol`, `serena_find_referencing_symbols`, etc. for navigating the codebase (e.g., tracing how `ScoringConfig` flows through parsing and grading).

## Student File Path Pattern

```
StudentAnswer_<COURSE>_<TYPE>_<ASSIGNMENT>_<DATE>/
  <STUDENT_ID>_<STUDENT_NAME>/
    <answer_file>.pdf|.docx|.doc
```

Example: `StudentAnswer_ISYS6599038_DFEA_LEC_Personal_Assignment_2_19.01.2026.19.16/2902737810_LUK SEKAR DADARI/TP2-ISYS6599 – MIS for Leader 2025_LUK SEKAR DADARI_2902737810.pdf`

## Prompt Customization

- Edit `Individual_Prompts.md` or `Group_Prompts.md` to change grading criteria, questions, and scoring.
- The number of questions is dynamic — add/remove `Question N: [score]/MAX` lines in the prompt and the script adapts.
- Feedback is always expected in **Bahasa Indonesia**.
