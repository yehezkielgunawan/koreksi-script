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

# Load base prompt from .rules
with open('.rules', 'r') as f:
    rules_content = f.read()
# Extract the base prompt between ---
base_prompt_match = re.search(r'---([\s\S]*?)---', rules_content)
if base_prompt_match:
    BASE_PROMPT = base_prompt_match.group(1).strip()
else:
    BASE_PROMPT = "This is my student essay. It's a personal assignment.\nYou are a grader for student essays. Please review the essay based on the details and the arguments made in the essay.\n\nPlease score each question (1-5) out of 20 points each, and provide a total score out of 100.\n\nOutput your results in this exact format:\n\nQuestion 1: [score]/20\nQuestion 2: [score]/20\nQuestion 3: [score]/20\nQuestion 4: [score]/20\nQuestion 5: [score]/20\n\nTotal Score: [total]/100\nFeedback: [in Bahasa Indonesia]\n\nFor the feedback, focus specifically on the question with the LOWEST score. Explain why the student received that low score and what specific improvements are needed for that particular question. Be detailed and constructive in your criticism."

def extract_text_from_pdf(file_path: str) -> str:
    text = ""
    with open(file_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text += page.extract_text() or ''
    return text

def get_student_name_from_filename(filename: str) -> str:
    name = os.path.splitext(os.path.basename(filename))[0]
    return name.replace('_', ' ').replace('-', ' ')

def call_gemini_api(prompt: str, essay: str, student_name: str):
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(f"{prompt}\n\n{essay}")
    return response.text

def parse_personal_response(response: str):
    # Extract scores for each question (1-5) and total score
    scores = {}
    for i in range(1, 6):
        match = re.search(rf"[Qq]uestion\s*{i}[^\d]*(\d{{1,2}})\s*/\s*20", response)
        if match:
            scores[f"num_{i}"] = int(match.group(1))
        else:
            scores[f"num_{i}"] = 0
    
    # Extract total score
    total_match = re.search(r'[Tt]otal\s*[Ss]core\s*[:\-]?\s*(\d{1,3})\s*/\s*100', response)
    total_score = int(total_match.group(1)) if total_match else sum(scores.values())
    
    # Extract feedback
    feedback_match = re.search(r'[Ff]eedback\s*[:\-]?\s*(.*)', response)
    feedback = feedback_match.group(1).strip() if feedback_match else ""
    
    return scores, total_score, feedback

def format_raw_response_as_json(response: str, scores: dict, total_score: int, feedback: str):
    """Format the raw response as structured JSON"""
    # Extract the structured parts from the response
    structured_response = {
        "question_scores": scores,
        "total_score": f"{total_score}/100",
        "feedback": feedback,
        "raw_text": response.strip()
    }
    return structured_response

def main():
    results = []
    # Find all StudentAnswer* directories
    for root, dirs, files in os.walk('.'):
        if os.path.basename(root).startswith('StudentAnswer'):
            # Recursively find all PDF and DOCX files in this directory
            for dirpath, _, filenames in os.walk(root):
                for filename in filenames:
                    if filename.lower().endswith('.pdf') or filename.lower().endswith('.docx'):
                        file_path = os.path.join(dirpath, filename)
                        print(f"Processing {file_path}...")
                        if file_path.endswith('.pdf'):
                            essay_text = extract_text_from_pdf(file_path)
                        else:
                            doc = Document(file_path)
                            essay_text = '\n'.join([para.text for para in doc.paragraphs])
                        student_name = get_student_name_from_filename(filename)
                        response = call_gemini_api(BASE_PROMPT, essay_text, student_name)
                        scores, total_score, feedback = parse_personal_response(response)
                        structured_raw_response = format_raw_response_as_json(response, scores, total_score, feedback)
                        result = {
                            'student_name': student_name,
                            'score': scores,
                            'total_score': f"{total_score}/100",
                            'feedback': feedback,
                            'raw_response': structured_raw_response,
                            'file_path': file_path
                        }
                        results.append(result)
                        print(f"{student_name}:")
                        for i in range(1, 6):
                            print(f"  Question {i}: {scores[f'num_{i}']}/20")
                        print(f"  Total Score: {total_score}/100")
                        print(f"  Feedback: {feedback}\n")
    # Save to JSON
    with open('personal_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    print("Results saved to personal_results.json")

if __name__ == "__main__":
    main() 