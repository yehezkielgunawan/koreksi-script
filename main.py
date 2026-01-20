"""
Student Personal Assignment Score Checker

This script reviews student essay assignments using the Gemini API.
It dynamically parses scoring configuration from the prompt file.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import google.generativeai as genai
import PyPDF2
from docx import Document
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=GOOGLE_API_KEY)

# Constants
PROMPT_FILE = "Individual_Prompts.md"
OUTPUT_FILE = "individual_results.json"
STUDENT_ANSWER_PREFIX = "StudentAnswer"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc"}
QUESTION_FILE_KEYWORDS = {"question", "soal", "pertanyaan"}

# Rate Limiting Constants (Gemini Free Tier: 5 RPM, 20 RPD)
REQUESTS_PER_MINUTE = 5
REQUESTS_PER_DAY = 20
SECONDS_BETWEEN_REQUESTS = 60 / REQUESTS_PER_MINUTE  # 12 seconds
MAX_RETRIES = 3
RETRY_BACKOFF_MULTIPLIER = 2  # Exponential backoff multiplier


class DailyLimitReachedException(Exception):
    """Exception raised when Gemini daily request limit is reached."""
    pass


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
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def extract_text_from_docx(file_path: Path) -> str:
    """Extract text content from a DOCX file."""
    doc = Document(str(file_path))
    return "\n".join(para.text for para in doc.paragraphs)


def extract_text(file_path: Path) -> str:
    """
    Extract text from a file based on its extension.

    Supports: PDF, DOCX, DOC
    """
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return extract_text_from_pdf(file_path)
    elif suffix in {".docx", ".doc"}:
        try:
            return extract_text_from_docx(file_path)
        except Exception as e:
            if suffix == ".doc":
                raise ValueError(
                    f"Legacy .doc format may not be fully supported: {file_path}"
                ) from e
            raise
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


# =============================================================================
# Student File Discovery
# =============================================================================


def is_question_file(filename: str) -> bool:
    """
    Check if a filename appears to be a question file (not a student answer).

    Question files typically contain words like 'question', 'soal', 'pertanyaan'.
    """
    filename_lower = filename.lower()
    return any(keyword in filename_lower for keyword in QUESTION_FILE_KEYWORDS)


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


def find_student_files(base_dir: Path = Path(".")) -> list[Path]:
    """
    Find all student answer files in StudentAnswer* directories.

    Filters out:
    - Question files (containing 'question', 'soal', etc.)
    - Files without supported extensions
    """
    files_to_process = []

    for item in base_dir.iterdir():
        if not item.is_dir() or not item.name.startswith(STUDENT_ANSWER_PREFIX):
            continue

        logger.info(f"Scanning directory: {item.name}")

        # Walk through all subdirectories
        for file_path in item.rglob("*"):
            if not file_path.is_file():
                continue

            # Check extension
            if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            # Skip question files
            if is_question_file(file_path.name):
                logger.debug(f"Skipping question file: {file_path.name}")
                continue

            files_to_process.append(file_path)

    # Sort by path for consistent ordering
    files_to_process.sort()

    return files_to_process


# =============================================================================
# Gemini API Integration with Rate Limiting
# =============================================================================


class RateLimiter:
    """
    Simple rate limiter to respect Gemini's free tier limits.

    Ensures minimum delay between API calls and tracks request timing.
    """

    def __init__(self, min_interval: float = SECONDS_BETWEEN_REQUESTS):
        self.min_interval = min_interval
        self.last_request_time: Optional[float] = None

    def wait_if_needed(self):
        """Wait if necessary to respect rate limits."""
        if self.last_request_time is None:
            self.last_request_time = time.time()
            return

        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            wait_time = self.min_interval - elapsed
            logger.info(f"Rate limit: waiting {wait_time:.1f}s before next request...")
            time.sleep(wait_time)

        self.last_request_time = time.time()


# Global rate limiter instance
_rate_limiter = RateLimiter()


def is_daily_limit_error(error_str: str) -> bool:
    """
    Check if the error indicates a daily quota limit has been reached.
    
    Based on actual Gemini error format:
    - quota_id: "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    - quota_value: 20
    """
    error_lower = error_str.lower()
    daily_indicators = [
        "perday",  # Matches "PerDayPerProject" in quota_id
        "per day",
        "daily limit",
        "daily quota",
        "freetier",  # Matches "FreeTier" in quota_id
        "generaterequestsperday",  # Matches the quota metric
        "requests_per_day",
    ]
    return any(indicator in error_lower for indicator in daily_indicators)


def call_gemini_api(prompt: str, essay_text: str) -> str:
    """
    Call the Gemini API to grade an essay with rate limiting and retry logic.

    Args:
        prompt: The grading prompt/instructions
        essay_text: The student's essay content

    Returns:
        The model's response text

    Raises:
        DailyLimitReachedException: If daily request limit is reached
        Exception: If all retries are exhausted
    """
    model = genai.GenerativeModel("gemini-3-flash-preview")
    full_prompt = f"{prompt}\n\nEssay to grade:\n{essay_text}"

    last_exception = None

    for attempt in range(MAX_RETRIES):
        try:
            # Wait for rate limit before making request
            _rate_limiter.wait_if_needed()

            response = model.generate_content(full_prompt)
            return response.text

        except Exception as e:
            last_exception = e
            error_str = str(e)

            # Check if daily limit is reached - don't retry, just raise immediately
            if is_daily_limit_error(error_str):
                logger.error(
                    "Daily API limit reached (20 requests per day). "
                    "Partial results will be saved. Please try again tomorrow."
                )
                raise DailyLimitReachedException(
                    "Gemini API daily limit (20 requests) reached. "
                    "Please wait until tomorrow to continue processing."
                ) from e

            # Check if it's a rate limit error (429) - per-minute limit, can retry
            if "429" in error_str or "quota" in error_str.lower():
                # Calculate backoff time
                retry_delay = SECONDS_BETWEEN_REQUESTS * (RETRY_BACKOFF_MULTIPLIER ** attempt)

                # Try to extract suggested retry delay from error message
                if "retry in" in error_str.lower():
                    retry_match = re.search(r"retry in (\d+\.?\d*)", error_str.lower())
                    if retry_match:
                        retry_delay = max(float(retry_match.group(1)) + 1, retry_delay)

                logger.warning(
                    f"Rate limit hit (attempt {attempt + 1}/{MAX_RETRIES}). "
                    f"Waiting {retry_delay:.1f}s before retry..."
                )
                time.sleep(retry_delay)
            else:
                # For other errors, use standard backoff
                retry_delay = 5 * (RETRY_BACKOFF_MULTIPLIER ** attempt)
                logger.warning(
                    f"API error (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                    f"Waiting {retry_delay:.1f}s before retry..."
                )
                time.sleep(retry_delay)

    # All retries exhausted
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
    Parse the Gemini response and extract scores.

    Args:
        response: Raw response from Gemini
        config: Scoring configuration with question details
        debug: If True, include raw response in output

    Returns:
        Dictionary with question_scores, total_score, feedback
    """
    if debug:
        logger.debug("Raw Gemini Response:\n%s", response)

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
    total_pattern = rf"[Tt]otal\s*[Ss]core\s*[:\-]?\s*(\d{{1,3}})\s*/\s*{config.total_max_score}"
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
    file_path: Path, prompt: str, config: ScoringConfig, debug: bool = False
) -> dict:
    """
    Process a single student file and return grading results.

    Args:
        file_path: Path to the student's answer file
        prompt: The grading prompt
        config: Scoring configuration
        debug: If True, include debug info in results

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

    # Call Gemini API
    response = call_gemini_api(prompt, essay_text)

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


def load_existing_results() -> tuple[list[dict], set[str]]:
    """
    Load existing results from the output file.

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

        existing_results = data.get("results", [])

        # Build set of file paths that have been successfully scored
        scored_paths = {result["file_path"] for result in existing_results}

        logger.info(f"Loaded {len(existing_results)} existing results from {OUTPUT_FILE}")
        return existing_results, scored_paths

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Could not parse existing results file: {e}")
        return [], set()


def is_already_scored(file_path: Path, scored_paths: set[str]) -> bool:
    """
    Check if a student file has already been scored.

    Args:
        file_path: Path to the student file
        scored_paths: Set of file paths that have been successfully scored

    Returns:
        True if the file has already been scored, False otherwise
    """
    return str(file_path) in scored_paths


def save_results_incrementally(
    config: ScoringConfig,
    results: list[dict],
    errors: list[dict],
    daily_limit_reached: bool = False,
):
    """
    Save results to JSON file immediately after each student is processed.
    
    This ensures data is not lost if the script crashes or hits API limits.
    
    Args:
        config: Scoring configuration
        results: List of all results (existing + new)
        errors: List of errors encountered
        daily_limit_reached: Whether daily limit was hit
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
        "daily_limit_reached": daily_limit_reached,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    logger.debug(f"Results saved incrementally ({len(results)} students)")


# =============================================================================
# Main Entry Point
# =============================================================================


def main(debug: bool = False):
    """
    Main function to process all student files.

    Args:
        debug: If True, enable debug logging and include raw responses
    """
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 60)
    print("Student Personal Assignment Score Checker")
    print("=" * 60)

    # Load prompt and configuration
    prompt, config = load_prompt_and_config()
    print(f"Loaded grading prompt from {PROMPT_FILE}")
    print(
        f"Configuration: {config.num_questions} questions, max score: {config.total_max_score}"
    )
    print("-" * 60)

    # Load existing results to skip already-scored students
    existing_results, scored_paths = load_existing_results()
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
        f for f in all_files if not is_already_scored(f, scored_paths)
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
    all_results = existing_results.copy()  # Start with existing results
    errors = []
    daily_limit_reached = False
    newly_scored_count = 0

    for idx, file_path in enumerate(files_to_process):
        try:
            result = process_file(file_path, prompt, config, debug=debug)
            all_results.append(result)
            newly_scored_count += 1
            
            # Save immediately after each successful grading
            save_results_incrementally(config, all_results, errors, daily_limit_reached)
            print(f"  [Saved to {OUTPUT_FILE}]")
            
        except DailyLimitReachedException as e:
            # Daily limit reached - results already saved incrementally
            daily_limit_reached = True
            print("\n" + "!" * 60)
            print("DAILY LIMIT REACHED")
            print("!" * 60)
            print(f"Gemini API daily limit (20 requests) has been reached.")
            print(f"Processed {newly_scored_count} students before limit was hit.")
            remaining = len(files_to_process) - idx
            print(f"Remaining {remaining} students will need to wait.")
            print("Please run the script again tomorrow to continue.")
            print("!" * 60)
            
            # Final save with daily_limit_reached flag
            save_results_incrementally(config, all_results, errors, daily_limit_reached=True)
            break
            
        except Exception as e:
            error_msg = f"Error processing {file_path}: {e}"
            logger.error(error_msg)
            errors.append({"file_path": str(file_path), "error": str(e)})
            
            # Save even when there's an error to preserve progress
            save_results_incrementally(config, all_results, errors, daily_limit_reached)

    # Final summary
    print("\n" + "=" * 60)

    print(f"Results saved to {OUTPUT_FILE}")
    print(f"Total scored students: {len(all_results)}")
    print(f"  - Previously scored: {len(existing_results)}")
    print(f"  - Newly scored: {newly_scored_count}")
    if errors:
        print(f"Errors: {len(errors)} files failed")
    
    if daily_limit_reached:
        remaining = len(files_to_process) - newly_scored_count
        print(f"\n⚠️  DAILY LIMIT REACHED - {remaining} students still pending")
        print("   Run the script again tomorrow to continue processing.")

    print("=" * 60)


if __name__ == "__main__":
    import sys

    # Simple debug flag support
    debug_mode = "--debug" in sys.argv or "-d" in sys.argv
    main(debug=debug_mode)
