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
    BASE_PROMPT = "This is my student essay. It's a group assignment.\nYou are a grader for student essays. It's only a quick ideation paper of each groups. Generate the score for each groups that I've given here from the PDF files.\nOutput your results in this exact format:\nSummary of score [score]/100\nFeedback: <what can be improved for the group? Any something missing or out of scope?>"

def extract_text_from_pdf(file_path: str) -> str:
    text = ""
    with open(file_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text += page.extract_text() or ''
    return text

def get_group_name_from_filename(filename: str) -> str:
    name = os.path.splitext(os.path.basename(filename))[0]
    return name.replace('_', ' ').replace('-', ' ')

def call_gemini_api(prompt: str, essay: str, group_name: str):
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(f"{prompt}\n\n{essay}")
    return response.text

def parse_group_response(response: str):
    # Extract score and feedback as per required format
    score_match = re.search(r'Summary of score\s*([\d]{1,3})/100', response)
    feedback_match = re.search(r'Feedback\s*[:\-]?\s*(.*)', response)
    score = int(score_match.group(1)) if score_match else None
    feedback = feedback_match.group(1).strip() if feedback_match else ""
    return score, feedback

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
                        group_name = get_group_name_from_filename(filename)
                        response = call_gemini_api(BASE_PROMPT, essay_text, group_name)
                        score, feedback = parse_group_response(response)
                        result = {
                            'group_name': group_name,
                            'score': score,
                            'feedback': feedback,
                            'raw_response': response,
                            'file_path': file_path
                        }
                        results.append(result)
                        print(f"{group_name}: Summary of score {score}/100\nFeedback: {feedback}\n")
    # Save to JSON
    with open('group_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    print("Results saved to group_results.json")

if __name__ == "__main__":
    main()
