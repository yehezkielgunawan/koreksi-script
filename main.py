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

def load_base_prompt():
    """Load base prompt from Individual_Prompts.md file"""
    try:
        with open('Individual_Prompts.md', 'r', encoding='utf-8') as f:
            content = f.read()

        # Extract the prompt content starting from "This is my student essay"
        lines = content.split('\n')
        prompt_lines = []
        start_collecting = False

        for line in lines:
            if line.strip().startswith("This is my student essay"):
                start_collecting = True

            if start_collecting:
                prompt_lines.append(line)

        return '\n'.join(prompt_lines).strip()

    except FileNotFoundError:
        # Fallback prompt if file doesn't exist
        return """This is my student essay. It's a personal assignment.

You are a grader for student essays. Please review the essay based on the details and the arguments made in the essay.

Please score each question (1-5) out of 20 points each, and provide a total score out of 100.

Output your results in this exact format:

Question 1: [score]/20
Question 2: [score]/20
Question 3: [score]/20
Question 4: [score]/20
Question 5: [score]/20

Total Score: [total]/100
Feedback: [in Bahasa Indonesia]

For the feedback, focus specifically on the question with the LOWEST score. Explain why the student received that low score for that answer and what specific improvements are needed for that particular question. Be detailed and constructive in your criticism."""

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

def get_student_name_from_filename(filename: str) -> str:
    """Extract student name from filename"""
    name = os.path.splitext(os.path.basename(filename))[0]
    return name.replace('_', ' ').replace('-', ' ')

def call_gemini_api(prompt: str, essay: str) -> str:
    """Call Gemini API to grade the essay"""
    model = genai.GenerativeModel('gemini-2.5-flash')
    full_prompt = f"{prompt}\n\nEssay to grade:\n{essay}"
    response = model.generate_content(full_prompt)
    return response.text

def parse_grading_response(response: str) -> dict:
    """Parse the Gemini response and return structured data"""
    # Extract individual question scores
    question_scores = []
    for i in range(1, 6):
        match = re.search(rf"[Qq]uestion\s*{i}[^\d]*(\d{{1,2}})\s*/\s*20", response)
        if match:
            question_scores.append(int(match.group(1)))
        else:
            question_scores.append(0)

    # Extract total score
    total_match = re.search(r'[Tt]otal\s*[Ss]core\s*[:\-]?\s*(\d{1,3})\s*/\s*100', response)
    total_scores = int(total_match.group(1)) if total_match else sum(question_scores)

    # Extract feedback
    feedback_match = re.search(r'[Ff]eedback\s*[:\-]?\s*(.*)', response, re.DOTALL)
    feedback = feedback_match.group(1).strip() if feedback_match else ""

    return {
        "question_scores": question_scores,
        "total_scores": total_scores,
        "feedback": feedback
    }

def process_file(file_path: str, base_prompt: str) -> dict:
    """Process a single file and return grading results"""
    print(f"Processing {file_path}...")

    # Extract text based on file type
    if file_path.lower().endswith('.pdf'):
        essay_text = extract_text_from_pdf(file_path)
    elif file_path.lower().endswith('.docx'):
        essay_text = extract_text_from_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_path}")

    # Get student name
    student_name = get_student_name_from_filename(os.path.basename(file_path))

    # Call Gemini API
    response = call_gemini_api(base_prompt, essay_text)

    # Parse response
    parsed_result = parse_grading_response(response)

    # Add metadata
    result = {
        'student_name': student_name,
        'file_path': file_path,
        **parsed_result
    }

    # Print results
    print(f"{student_name}:")
    for i, score in enumerate(parsed_result['question_scores'], 1):
        print(f"  Question {i}: {score}/20")
    print(f"  Total Score: {parsed_result['total_scores']}/100")
    print(f"  Feedback: {parsed_result['feedback'][:100]}..." if len(parsed_result['feedback']) > 100 else f"  Feedback: {parsed_result['feedback']}")
    print()

    return result

def find_student_files():
    """Find all PDF and DOCX files in StudentAnswer* directories"""
    files_to_process = []

    for root, dirs, files in os.walk('.'):
        if os.path.basename(root).startswith('StudentAnswer'):
            for dirpath, _, filenames in os.walk(root):
                for filename in filenames:
                    if filename.lower().endswith(('.pdf', '.docx', 'doc')):
                        file_path = os.path.join(dirpath, filename)
                        files_to_process.append(file_path)

    return files_to_process

def main():
    """Main function to process all student files"""
    # Load base prompt
    base_prompt = load_base_prompt()
    print("Loaded grading prompt from Individual_Prompts.md")
    print("-" * 50)

    # Find all student files
    files_to_process = find_student_files()

    if not files_to_process:
        print("No student files found in StudentAnswer* directories.")
        return

    print(f"Found {len(files_to_process)} files to process.")
    print("-" * 50)

    # Process all files
    results = []
    for file_path in files_to_process:
        try:
            result = process_file(file_path, base_prompt)
            results.append(result)
        except Exception as e:
            print(f"Error processing {file_path}: {str(e)}")
            continue

    # Save results to JSON
    output_file = 'individual_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Results saved to {output_file}")
    print(f"Processed {len(results)} files successfully.")

if __name__ == "__main__":
    main()
