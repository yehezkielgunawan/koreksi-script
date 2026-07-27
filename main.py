"""
Student Personal Assignment Score Checker

This script reviews student essay assignments using OpenRouter API
with the Qwen3 235B A22B model.
It dynamically parses scoring configuration from the prompt file.
"""

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import pypdf
from docx import Document
from dotenv import load_dotenv
from openai import OpenAI

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not found in .env file")

# OpenRouter client (OpenAI-compatible)
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)
# OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
OPENROUTER_MODEL = "google/gemma-4-31b-it:free"

# Constants
PROMPT_FILE = "Individual_Prompts.md"
OUTPUT_FILE = "individual_results.json"
STUDENT_ANSWER_PREFIX = "StudentAnswer"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc"}
SKIP_FILE_KEYWORDS = {
    "question",
    "soal",
    "pertanyaan",  # Question files
    "attachment",  # Question attachments
    "ai_usage",
    "ai form",
    "declaration",  # AI declaration forms
}

# Rate Limiting Constants
# OpenRouter limits (https://openrouter.ai/docs/api/reference/limits):
#   - Free models (`:free` suffix): 20 RPM (60s / 20 = 3s minimum spacing)
#   - Paid / pay-as-you-go models: no platform-level rate limit
# We auto-detect from the model ID and skip the local delay for paid models;
# the 429 retry logic below still acts as a safety net either way.
IS_FREE_MODEL = OPENROUTER_MODEL.endswith(":free")
SECONDS_BETWEEN_REQUESTS = 3.0 if IS_FREE_MODEL else 0.0
MAX_RETRIES = 3
RETRY_BACKOFF_MULTIPLIER = 2


@dataclass
class QuestionConfig:
    """Configuration for a single question's scoring."""

    number: int
    max_score: int


@dataclass
class ScoringConfig:
    """Configuration for the entire assignment scoring."""

    questions: list[QuestionConfig] = field(default_factory=list)
    total_max_score: int = 100

    @property
    def num_questions(self) -> int:
        return len(self.questions)

    def get_max_score(self, question_number: int) -> int:
        """Get max score for a specific question number."""
        for q in self.questions:
            if q.number == question_number:
                return q.max_score
        return 0


def parse_scoring_config_from_prompt(prompt_content: str) -> ScoringConfig:
    """
    Parse the scoring configuration from the prompt content.

    Looks for patterns like:
    - Question 1: [score]/25
    - Question 2: [score]/15
    - Total Score: [total]/100
    """
    questions = []

    # Find all question score patterns: "Question N: [score]/MAX"
    question_pattern = re.compile(
        r"[Qq]uestion\s*(\d+)\s*:\s*\[score\]\s*/\s*(\d+)", re.IGNORECASE
    )
    matches = question_pattern.findall(prompt_content)

    for match in matches:
        question_num = int(match[0])
        max_score = int(match[1])
        questions.append(QuestionConfig(number=question_num, max_score=max_score))

    # Sort by question number
    questions.sort(key=lambda q: q.number)

    # Find total max score
    total_pattern = re.compile(
        r"[Tt]otal\s*[Ss]core\s*:\s*\[total\]\s*/\s*(\d+)", re.IGNORECASE
    )
    total_match = total_pattern.search(prompt_content)
    total_max = int(total_match.group(1)) if total_match else 100

    config = ScoringConfig(questions=questions, total_max_score=total_max)

    if not questions:
        logger.warning(
            "No question configuration found in prompt. Using default (4 questions at 25 points each)."
        )
        config.questions = [QuestionConfig(number=i, max_score=25) for i in range(1, 5)]

    logger.info(
        f"Scoring config: {config.num_questions} questions, total max score: {config.total_max_score}"
    )
    for q in config.questions:
        logger.debug(f"  Question {q.number}: max {q.max_score} points")

    return config


def load_prompt_and_config() -> tuple[str, ScoringConfig]:
    """
    Load the base prompt from Individual_Prompts.md and parse scoring configuration.

    Returns:
        Tuple of (prompt_content, scoring_config)
    """
    prompt_path = Path(PROMPT_FILE)

    if not prompt_path.exists():
        logger.warning(f"{PROMPT_FILE} not found. Using default prompt.")
        default_prompt = get_default_prompt()
        return default_prompt, parse_scoring_config_from_prompt(default_prompt)

    content = prompt_path.read_text(encoding="utf-8")

    # Extract prompt starting from "This is my student"
    lines = content.split("\n")
    prompt_lines = []
    start_collecting = False

    for line in lines:
        if line.strip().startswith("This is my student"):
            start_collecting = True
        if start_collecting:
            prompt_lines.append(line)

    prompt = "\n".join(prompt_lines).strip()

    if not prompt:
        logger.warning("Could not extract prompt content. Using default.")
        prompt = get_default_prompt()

    config = parse_scoring_config_from_prompt(prompt)

    return prompt, config


def get_default_prompt() -> str:
    """Return the default grading prompt."""
    return """This is my student essay. It's a personal assignment.

You are a grader for student essays. Please review the essay based on the details and the arguments made in the essay. The more explanatory the answer, the better the score.

Please score each question (1-4) out of 25 points each, and provide a total score out of 100.

Output your results in this exact format:

Question 1: [score]/25
Question 2: [score]/25
Question 3: [score]/25
Question 4: [score]/25

Total Score: [total]/100
Feedback: [in Bahasa Indonesia]

For the feedback, focus specifically on the question with the LOWEST score. Explain why the student received that low score for that answer and what specific improvements are needed for that particular question. Just summarize the feedback in 2-3 sentences."""


# =============================================================================
# File Extraction Functions
# =============================================================================


def extract_text_from_pdf(file_path: Path) -> str:
    """Extract text content from a PDF file."""
    text_parts = []
    with open(file_path, "rb") as f:
        reader = pypdf.PdfReader(f)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def extract_text_from_docx(file_path: Path) -> str:
    """Extract text content from a DOCX file."""
    doc = Document(str(file_path))
    return "\n".join(para.text for para in doc.paragraphs)


def extract_text_from_doc(file_path: Path) -> str:
    """
    Extract text from a legacy .doc file using macOS textutil.

    Falls back to python-docx if textutil is not available (non-macOS).
    """
    try:
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(file_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        logger.warning(
            f"textutil returned empty/error for {file_path.name}, trying python-docx fallback"
        )
    except FileNotFoundError:
        logger.warning(
            "textutil not found (not on macOS?), trying python-docx fallback"
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            f"textutil timed out for {file_path.name}, trying python-docx fallback"
        )

    return extract_text_from_docx(file_path)


class HTMLStripper(HTMLParser):
    """HTML parser that extracts plain text."""

    def __init__(self):
        super().__init__()
        self.text_parts: list[str] = []

    def handle_data(self, data: str):
        self.text_parts.append(data)

    def get_text(self) -> str:
        return "".join(self.text_parts)


def extract_text_from_html(file_path: Path) -> str:
    """Extract plain text from an HTML file."""
    content = file_path.read_text(encoding="utf-8")
    stripper = HTMLStripper()
    stripper.feed(content)
    text = stripper.get_text()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_text(file_path: Path) -> str:
    """
    Extract text from a file based on its extension.

    Supports: PDF, DOCX, DOC
    """
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return extract_text_from_pdf(file_path)
    elif suffix == ".doc":
        return extract_text_from_doc(file_path)
    elif suffix == ".docx":
        return extract_text_from_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


# =============================================================================
# Student File Discovery
# =============================================================================


def is_non_answer_file(filename: str) -> bool:
    """
    Check if a filename is NOT a student answer (i.e., should be skipped).

    Skips:
    - Question files (containing 'question', 'soal', 'pertanyaan')
    - Question attachments (containing 'attachment')
    - AI declaration forms (containing 'ai_usage', 'ai form', 'declaration')
    """
    filename_lower = filename.lower()
    return any(keyword in filename_lower for keyword in SKIP_FILE_KEYWORDS)


def get_student_name_from_path(file_path: Path) -> str:
    """
    Extract student name from file path.

    Tries to extract from parent directory name first (common pattern),
    falls back to filename.
    """
    # Try to get student ID and name from parent directory
    # Pattern: "2902737810_LUK SEKAR DADARI" -> "2902737810 LUK SEKAR DADARI"
    parent_name = file_path.parent.name

    # Check if parent looks like a student folder (starts with digits)
    if parent_name and re.match(r"^\d+_", parent_name):
        return parent_name.replace("_", " ")

    # Fall back to filename without extension
    name = file_path.stem
    return name.replace("_", " ").replace("-", " ")


def find_student_files(base_dir: Path = Path(".")) -> list[tuple[Path, Optional[Path]]]:
    """
    Find student answer files in StudentAnswer* directories.

    For each student folder, picks exactly ONE answer file by:
    1. Filtering out non-answer files (questions, attachments, AI forms)
    2. Filtering out unsupported extensions
    3. If multiple candidates remain, picking the first one (sorted by name)

    Also looks for Question.html in each student folder for per-student questions.

    Returns:
        List of (answer_file_path, question_html_path_or_None) tuples.

    Logs skipped files for transparency.
    """
    files_to_process: list[tuple[Path, Optional[Path]]] = []

    for item in base_dir.iterdir():
        if not item.is_dir() or not item.name.startswith(STUDENT_ANSWER_PREFIX):
            continue

        logger.info(f"Scanning directory: {item.name}")

        # Group files by student folder (immediate subdirectories)
        student_folders = [d for d in item.iterdir() if d.is_dir()]

        for student_folder in sorted(student_folders):
            candidates = []
            question_html_path: Optional[Path] = None

            for file_path in student_folder.rglob("*"):
                if not file_path.is_file():
                    continue

                # Check for Question.html
                if file_path.name.lower() == "question.html":
                    question_html_path = file_path
                    continue

                # Skip unsupported extensions
                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue

                # Skip non-answer files
                if is_non_answer_file(file_path.name):
                    logger.debug(f"Skipping non-answer file: {file_path.name}")
                    continue

                candidates.append(file_path)

            if not candidates:
                logger.warning(
                    f"No answer file found for student: {student_folder.name}"
                )
                continue

            if len(candidates) > 1:
                logger.warning(
                    f"Multiple answer candidates for {student_folder.name}: "
                    f"{[c.name for c in candidates]}. Using first one."
                )

            # Pick the first candidate (sorted by name for consistency)
            candidates.sort(key=lambda p: p.name)

            if question_html_path:
                logger.debug(f"Found Question.html for {student_folder.name}")
            else:
                logger.debug(f"No Question.html found for {student_folder.name}")

            files_to_process.append((candidates[0], question_html_path))

    files_to_process.sort(key=lambda x: x[0])

    return files_to_process


# =============================================================================
# OpenRouter API Integration
# =============================================================================


class RateLimiter:
    """Ensures minimum delay between API calls.

    When `min_interval` is 0 (e.g. paid OpenRouter models with no platform
    rate limit), this becomes a no-op and adds zero overhead per request.
    """

    def __init__(self, min_interval: float = SECONDS_BETWEEN_REQUESTS):
        self.min_interval = min_interval
        self.last_request_time: Optional[float] = None

    def wait_if_needed(self):
        if self.min_interval <= 0:
            return

        if self.last_request_time is None:
            self.last_request_time = time.time()
            return

        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            wait_time = self.min_interval - elapsed
            logger.info(f"Rate limit: waiting {wait_time:.1f}s before next request...")
            time.sleep(wait_time)

        self.last_request_time = time.time()


_rate_limiter = RateLimiter()


def call_llm_api(prompt: str, essay_text: str) -> str:
    """
    Call OpenRouter API (Qwen3 235B A22B) to grade an essay.

    Uses the OpenAI-compatible endpoint with retry logic.
    """
    full_prompt = f"{prompt}\n\nEssay to grade:\n{essay_text}"

    last_exception = None

    for attempt in range(MAX_RETRIES):
        try:
            _rate_limiter.wait_if_needed()

            response = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[{"role": "user", "content": full_prompt}],
            )

            content = response.choices[0].message.content
            if not content:
                raise ValueError("Empty response from API")
            return content

        except Exception as e:
            last_exception = e
            error_str = str(e)

            if "429" in error_str or "rate" in error_str.lower():
                base_delay = (
                    SECONDS_BETWEEN_REQUESTS if SECONDS_BETWEEN_REQUESTS > 0 else 5
                )
                retry_delay = base_delay * (RETRY_BACKOFF_MULTIPLIER ** (attempt + 1))
                logger.warning(
                    f"Rate limit hit (attempt {attempt + 1}/{MAX_RETRIES}). "
                    f"Waiting {retry_delay:.1f}s..."
                )
                time.sleep(retry_delay)
            elif (
                "402" in error_str
                or "insufficient" in error_str.lower()
                or "credits" in error_str.lower()
            ):
                logger.error(
                    "Insufficient credits on OpenRouter. Please top up your balance."
                )
                raise
            else:
                retry_delay = 5 * (RETRY_BACKOFF_MULTIPLIER**attempt)
                logger.warning(
                    f"API error (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                    f"Retrying in {retry_delay:.1f}s..."
                )
                time.sleep(retry_delay)

    raise last_exception


# =============================================================================
# Response Parsing
# =============================================================================


@dataclass
class GradingResult:
    """Structured result from grading a student's essay."""

    student_name: str
    file_path: str
    question_scores: dict[int, int]  # question_number -> score
    total_score: int
    feedback: str
    raw_response: Optional[str] = None  # For debugging


def parse_grading_response(
    response: str, config: ScoringConfig, debug: bool = False
) -> dict:
    """
    Parse the LLM response and extract scores.

    Args:
        response: Raw response from the LLM
        config: Scoring configuration with question details
        debug: If True, include raw response in output

    Returns:
        Dictionary with question_scores, total_score, feedback
    """
    if debug:
        logger.debug("Raw LLM Response:\n%s", response)

    question_scores = {}

    # Parse each question's score based on configuration
    for q in config.questions:
        # Try pattern with specific max score first
        pattern = rf"[Qq]uestion\s*{q.number}\s*[:\s]*(\d{{1,2}})\s*/\s*{q.max_score}"
        match = re.search(pattern, response)

        if not match:
            # Try more flexible pattern (any denominator)
            pattern = rf"[Qq]uestion\s*{q.number}\s*[:\s]*(\d{{1,2}})\s*/\s*\d+"
            match = re.search(pattern, response)

        if match:
            score = int(match.group(1))
            # Cap at max score for this question
            question_scores[q.number] = min(score, q.max_score)
        else:
            logger.warning(f"Could not find score for Question {q.number}")
            question_scores[q.number] = 0

    # Extract total score
    total_pattern = (
        rf"[Tt]otal\s*[Ss]core\s*[:\-]?\s*(\d{{1,3}})\s*/\s*{config.total_max_score}"
    )
    total_match = re.search(total_pattern, response)

    if total_match:
        total_score = int(total_match.group(1))
    else:
        # Fall back to sum of question scores
        total_score = sum(question_scores.values())
        logger.warning(
            f"Could not find total score in response. Calculated: {total_score}"
        )

    # Extract feedback
    feedback_match = re.search(r"[Ff]eedback\s*[:\-]?\s*(.*)", response, re.DOTALL)
    feedback = feedback_match.group(1).strip() if feedback_match else ""

    result = {
        "question_scores": question_scores,
        "total_score": total_score,
        "feedback": feedback,
    }

    if debug:
        result["raw_response"] = response

    return result


# =============================================================================
# File Processing
# =============================================================================


def process_file(
    file_path: Path,
    prompt: str,
    config: ScoringConfig,
    debug: bool = False,
    question_html_path: Optional[Path] = None,
) -> dict:
    """
    Process a single student file and return grading results.

    Args:
        file_path: Path to the student's answer file
        prompt: The grading prompt (may contain {QUESTIONS} placeholder)
        config: Scoring configuration
        debug: If True, include debug info in results
        question_html_path: Optional path to student's Question.html

    Returns:
        Dictionary with student name, scores, and feedback
    """
    logger.info(f"Processing: {file_path.name}")

    # Extract text from file
    essay_text = extract_text(file_path)

    if not essay_text.strip():
        raise ValueError(f"No text content extracted from {file_path}")

    # Get student name
    student_name = get_student_name_from_path(file_path)

    # Build final prompt with per-student questions if available
    final_prompt = prompt
    if "{QUESTIONS}" in prompt:
        if question_html_path and question_html_path.exists():
            questions_text = extract_text_from_html(question_html_path)
            final_prompt = prompt.replace("{QUESTIONS}", questions_text)
            logger.info(f"Injected questions from {question_html_path.name}")
        else:
            logger.warning(
                f"Prompt has {{QUESTIONS}} placeholder but no Question.html found for {student_name}. "
                "Using prompt as-is."
            )

    # Call LLM API
    response = call_llm_api(final_prompt, essay_text)

    # Parse response
    parsed = parse_grading_response(response, config, debug=debug)

    # Build result
    result = {
        "student_name": student_name,
        "file_path": str(file_path),
        **parsed,
    }

    # Print summary
    print_result_summary(student_name, parsed, config)

    return result


def print_result_summary(student_name: str, parsed: dict, config: ScoringConfig):
    """Print a formatted summary of grading results."""
    print(f"\n{student_name}:")

    for q in config.questions:
        score = parsed["question_scores"].get(q.number, 0)
        print(f"  Question {q.number}: {score}/{q.max_score}")

    print(f"  Total Score: {parsed['total_score']}/{config.total_max_score}")

    feedback = parsed["feedback"]
    if len(feedback) > 100:
        print(f"  Feedback: {feedback[:100]}...")
    else:
        print(f"  Feedback: {feedback}")


# =============================================================================
# Existing Results Management
# =============================================================================


RE_REVIEW_THRESHOLD = 80


def load_existing_results(re_review: bool = False) -> tuple[list[dict], set[str]]:
    """
    Load existing results from the output file.

    When re_review=True, students who scored below REREVIEW_THRESHOLD
    are removed from existing results so they get re-graded.

    Returns:
        Tuple of (existing_results_list, set_of_scored_file_paths)
    """
    output_path = Path(OUTPUT_FILE)

    if not output_path.exists():
        logger.info(f"No existing results file found at {OUTPUT_FILE}")
        return [], set()

    try:
        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        all_results = data.get("results", [])

        if re_review:
            kept = []
            re_review_count = 0
            for result in all_results:
                if result.get("total_score", 0) < RE_REVIEW_THRESHOLD:
                    re_review_count += 1
                    logger.info(
                        f"Queuing re-review: {result['student_name']} "
                        f"(score: {result.get('total_score', 0)})"
                    )
                else:
                    kept.append(result)
            if re_review_count:
                print(
                    f"Re-review mode: {re_review_count} students scored below "
                    f"{RE_REVIEW_THRESHOLD} will be re-graded."
                )
            all_results = kept

        scored_paths = {result["file_path"] for result in all_results}

        logger.info(f"Loaded {len(all_results)} existing results from {OUTPUT_FILE}")
        return all_results, scored_paths

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Could not parse existing results file: {e}")
        return [], set()


def is_already_scored(file_path: Path, scored_paths: set[str]) -> bool:
    """Check if a student file has already been scored."""
    return str(file_path) in scored_paths


def save_results_incrementally(
    config: ScoringConfig,
    results: list[dict],
    errors: list[dict],
):
    """
    Save results to JSON file immediately after each student is processed.

    This ensures data is not lost if the script crashes or hits API limits.
    """
    output_path = Path(OUTPUT_FILE)
    output_data = {
        "config": {
            "num_questions": config.num_questions,
            "questions": [
                {"number": q.number, "max_score": q.max_score} for q in config.questions
            ],
            "total_max_score": config.total_max_score,
        },
        "results": results,
        "errors": errors if errors else None,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    logger.debug(f"Results saved incrementally ({len(results)} students)")


# =============================================================================
# Main Entry Point
# =============================================================================


def main(debug: bool = False, re_review: bool = False):
    """
    Main function to process all student files.

    Args:
        debug: If True, enable debug logging and include raw responses
        re_review: If True, re-grade students who scored below the threshold
    """
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 60)
    print("Student Personal Assignment Score Checker")
    if re_review:
        print(f"  MODE: Re-review (re-grading scores below {RE_REVIEW_THRESHOLD})")
    tier = "free (20 RPM)" if IS_FREE_MODEL else "paid (no rate limit)"
    print(f"  Model: {OPENROUTER_MODEL} [{tier}]")
    if SECONDS_BETWEEN_REQUESTS > 0:
        print(f"  Min interval between requests: {SECONDS_BETWEEN_REQUESTS}s")
    else:
        print("  Min interval between requests: 0s (no local throttle)")
    print("=" * 60)

    # Load prompt and configuration
    prompt, config = load_prompt_and_config()
    print(f"Loaded grading prompt from {PROMPT_FILE}")
    print(
        f"Configuration: {config.num_questions} questions, max score: {config.total_max_score}"
    )
    print("-" * 60)

    # Load existing results to skip already-scored students
    existing_results, scored_paths = load_existing_results(re_review=re_review)
    if scored_paths:
        print(f"Found {len(scored_paths)} students already scored (will be skipped)")
    print("-" * 60)

    # Find student files
    all_files = find_student_files()

    if not all_files:
        print(f"No student files found in {STUDENT_ANSWER_PREFIX}* directories.")
        print("Make sure student answer folders are in the current directory.")
        return

    # Filter out already-scored files
    files_to_process = [
        (f, q) for f, q in all_files if not is_already_scored(f, scored_paths)
    ]

    print(f"Found {len(all_files)} total student files.")
    print(f"Skipping {len(all_files) - len(files_to_process)} already-scored students.")
    print(f"Processing {len(files_to_process)} new/unscored students.")
    print("-" * 60)

    if not files_to_process:
        print("All students have already been scored. Nothing to process.")
        print("=" * 60)
        return

    # Process new files only with incremental saving
    all_results = existing_results.copy()
    errors = []
    newly_scored_count = 0

    for idx, (file_path, question_html_path) in enumerate(files_to_process):
        try:
            result = process_file(
                file_path,
                prompt,
                config,
                debug=debug,
                question_html_path=question_html_path,
            )
            all_results.append(result)
            newly_scored_count += 1

            save_results_incrementally(config, all_results, errors)
            print(f"  [Saved to {OUTPUT_FILE}]")

        except Exception as e:
            error_msg = f"Error processing {file_path}: {e}"
            logger.error(error_msg)
            errors.append({"file_path": str(file_path), "error": str(e)})

            save_results_incrementally(config, all_results, errors)

    # Final summary
    print("\n" + "=" * 60)

    print(f"Results saved to {OUTPUT_FILE}")
    print(f"Total scored students: {len(all_results)}")
    print(f"  - Previously scored: {len(existing_results)}")
    print(f"  - Newly scored: {newly_scored_count}")
    if errors:
        print(f"Errors: {len(errors)} files failed")

    print("=" * 60)


if __name__ == "__main__":
    import sys

    debug_mode = "--debug" in sys.argv or "-d" in sys.argv
    re_review_mode = "--re-review" in sys.argv
    main(debug=debug_mode, re_review=re_review_mode)
