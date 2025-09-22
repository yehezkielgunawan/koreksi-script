import os
import json
import re
import PyPDF2
import google.generativeai as genai
from dotenv import load_dotenv
from docx import Document

# Load Gemini API key from .env
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
genai.configure(api_key=GOOGLE_API_KEY)


def load_base_prompt() -> str:
    """Load base prompt from Group_Prompts.md file.

    Starts collecting from the first line that begins with
    "This is my student" to allow minor wording variations.
    """
    try:
        with open('Group_Prompts.md', 'r', encoding='utf-8') as f:
            content = f.read()

        lines = content.split('\n')
        prompt_lines = []
        start_collecting = False

        for line in lines:
            if line.strip().startswith("This is my student"):
                start_collecting = True
            if start_collecting:
                prompt_lines.append(line)

        collected = '\n'.join(prompt_lines).strip()
        if collected:
            return collected
    except FileNotFoundError:
        pass

    # Fallback prompt if file doesn't exist or format unexpected
    return (
        "This is my student paper draft. It's a group assignment.\n\n"
        "You are a grader for student paper assignments. Please review the draft based on the details and the "
        "arguments made in the paper draft. The more explanatory the arguments, the better the score.\n\n"
        "Score: [total]/100\n"
        "Feedback: [in Bahasa Indonesia]\n\n"
        "For the feedback, summarize improvements in 2-3 sentences."
    )


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text content from PDF file"""
    text = ""
    with open(file_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text += page.extract_text() or ''
    return text


def extract_text_from_docx(file_path: str) -> str:
    """Extract text content from DOCX file"""
    doc = Document(file_path)
    return '\n'.join([para.text for para in doc.paragraphs])


def get_group_name_from_filename(filename: str) -> str:
    """Extract group name from filename"""
    name = os.path.splitext(os.path.basename(filename))[0]
    return name.replace('_', ' ').replace('-', ' ')


def call_gemini_api(prompt: str, draft_text: str) -> str:
    """Call Gemini API to grade the group paper draft"""
    model = genai.GenerativeModel('gemini-2.5-flash')
    full_prompt = f"{prompt}\n\nPaper draft to grade:\n{draft_text}"
    response = model.generate_content(full_prompt)
    return response.text


def parse_group_response(response: str) -> dict:
    """Parse the Gemini response and return structured data for groups.

    Supports either "Score: X/100" or "Summary of score X/100" formats.
    """
    # Score patterns: prefer explicit "Score"; fallback to "Summary of score"
    score_match = re.search(r'[Ss]core\s*[:\-]?\s*(\d{1,3})\s*/\s*100', response)
    if not score_match:
        score_match = re.search(r'[Ss]ummary\s+of\s+score\s*(\d{1,3})\s*/\s*100', response)
    score = int(score_match.group(1)) if score_match else None

    # Feedback: capture rest of text; be tolerant of formatting
    feedback_match = re.search(r'[Ff]eedback\s*[:\-]?\s*(.*)', response, re.DOTALL)
    feedback = feedback_match.group(1).strip() if feedback_match else ""

    return {
        "score": score,
        "feedback": feedback,
    }


def process_file(file_path: str, base_prompt: str) -> dict:
    """Process a single file and return grading results for a group draft"""
    print(f"Processing {file_path}...")

    # Extract text based on file type
    if file_path.lower().endswith('.pdf'):
        draft_text = extract_text_from_pdf(file_path)
    elif file_path.lower().endswith('.docx'):
        draft_text = extract_text_from_docx(file_path)
    elif file_path.lower().endswith('.doc'):
        # Basic support: attempt to open via python-docx may fail for .doc; skip gracefully
        try:
            draft_text = extract_text_from_docx(file_path)
        except Exception:
            raise ValueError(f"Unsupported legacy DOC format: {file_path}")
    else:
        raise ValueError(f"Unsupported file type: {file_path}")

    group_name = get_group_name_from_filename(os.path.basename(file_path))

    # Call Gemini API
    response_text = call_gemini_api(base_prompt, draft_text)

    # Parse response
    parsed = parse_group_response(response_text)

    result = {
        'group_name': group_name,
        'file_path': file_path,
        **parsed,
    }

    # Print summary
    print(f"{group_name}:")
    print(f"  Score: {parsed['score']}/100" if parsed['score'] is not None else "  Score: (not found)")
    preview_feedback = parsed['feedback']
    print(
        f"  Feedback: {preview_feedback[:100]}..." if len(preview_feedback) > 100 else f"  Feedback: {preview_feedback}"
    )
    print()

    return result


def find_student_files() -> list:
    """Find all PDF and DOCX files in StudentAnswer* directories"""
    files_to_process = []
    for root, dirs, files in os.walk('.'):
        if os.path.basename(root).startswith('StudentAnswer'):
            for dirpath, _, filenames in os.walk(root):
                for filename in filenames:
                    if filename.lower().endswith(('.pdf', '.docx', '.doc')):
                        files_to_process.append(os.path.join(dirpath, filename))
    return files_to_process


def main():
    """Main function to process all group drafts"""
    # Load base prompt
    base_prompt = load_base_prompt()
    print("Loaded grading prompt from Group_Prompts.md")
    print("-" * 50)

    # Find all student files
    files_to_process = find_student_files()

    if not files_to_process:
        print("No group files found in StudentAnswer* directories.")
        return

    print(f"Found {len(files_to_process)} files to process.")
    print("-" * 50)

    # Process all files
    results = []
    for file_path in files_to_process:
        try:
            result = process_file(file_path, base_prompt)
            results.append(result)
        except Exception as exc:
            print(f"Error processing {file_path}: {str(exc)}")
            continue

    # Save results to JSON
    output_file = 'group_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Results saved to {output_file}")
    print(f"Processed {len(results)} files successfully.")


if __name__ == "__main__":
    main()
