import os
import json
import glob
from dotenv import load_dotenv
from typing import List
from docx import Document
import PyPDF2
import google.generativeai as genai
from pydantic import BaseModel
import re

# Load Gemini API key from .env
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
genai.configure(api_key=GOOGLE_API_KEY)

# Load base prompt from .rules
with open('.rules', 'r') as f:
    rules_content = f.read()
# Extract the base prompt between ---
base_prompt_match = re.search(r'---([\s\S]*?)---', rules_content)
if base_prompt_match:
    BASE_PROMPT = base_prompt_match.group(1).strip()
else:
    BASE_PROMPT = "Please review the following student essay. Provide feedback and a score."

def extract_text_from_pdf(file_path: str) -> str:
    text = ""
    with open(file_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text += page.extract_text() or ''
    return text

def extract_text_from_docx(file_path: str) -> str:
    doc = Document(file_path)
    return '\n'.join([para.text for para in doc.paragraphs])

def get_student_name_from_filename(filename: str) -> str:
    # Remove extension and replace underscores/dashes with spaces
    name = os.path.splitext(os.path.basename(filename))[0]
    return name.replace('_', ' ').replace('-', ' ')

class ScoreDetail(BaseModel):
    problem_number: int
    score: int

class StudentReview(BaseModel):
    student_name: str
    score_details: List[ScoreDetail]
    overall_feedback: str  # One-line summary, focused on lowest score

def call_gemini_api(prompt: str, essay: str, student_name: str) -> StudentReview:
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(f"{prompt}\n\n{essay}")
    response_text = response.text
    # Parse the response text to extract scores and feedback
    score_details, overall_feedback = parse_gemini_response(response_text)
    return StudentReview(
        student_name=student_name,
        score_details=score_details,
        overall_feedback=overall_feedback
    )

def parse_gemini_response(response: str):
    # Try to extract scores for problems 1-5 and a one-line feedback
    # Example expected format in response:
    # Problem 1: 18/20\nProblem 2: 15/20\n...\nFeedback: ...
    score_details = []
    for i in range(1, 6):
        match = re.search(rf"[Pp]roblem\s*{i}[^\d]*(\d{{1,2}})\s*/\s*20", response)
        if match:
            score = int(match.group(1))
            score_details.append(ScoreDetail(problem_number=i, score=score))
        else:
            # If not found, assume 0 or missing
            score_details.append(ScoreDetail(problem_number=i, score=0))
    # Try to extract a one-line feedback (first line with 'feedback' or last line)
    feedback_match = re.search(r'[Ff]eedback\s*[:\-]?\s*(.*)', response)
    if feedback_match:
        overall_feedback = feedback_match.group(1).strip()
    else:
        # fallback: last non-empty line
        lines = [l.strip() for l in response.splitlines() if l.strip()]
        overall_feedback = lines[-1] if lines else ""
    return score_details, overall_feedback

def main():
    results = []
    for file_path in glob.glob('*.pdf') + glob.glob('*.docx'):
        print(f"Processing {file_path}...")
        if file_path.endswith('.pdf'):
            essay_text = extract_text_from_pdf(file_path)
        else:
            essay_text = extract_text_from_docx(file_path)
        student_name = get_student_name_from_filename(file_path)
        review = call_gemini_api(BASE_PROMPT, essay_text, student_name)
        results.append(review.dict())
    # Save to JSON
    with open('results.json', 'w') as f:
        json.dump(results, f, indent=2)
    print("Results saved to results.json")

if __name__ == "__main__":
    main() 