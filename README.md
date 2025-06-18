# Koreksi Script

A Python tool to automatically review and score student essays in PDF or DOCX format using Google Gemini (Gemini API).

## Quick Start

### 1. Clone the repository
```sh
git clone <your-repo-url>
cd koreksi-script
```

### 2. Set up your environment

#### Using a virtual environment (recommended)
```sh
python3 -m venv .venv
source .venv/bin/activate
```

#### Or, using [uv](https://github.com/astral-sh/uv) (fast Python package manager)
```sh
uv venv .venv
source .venv/bin/activate
```

### 3. Install dependencies
```sh
pip install -r requirements.txt
```
Or, with uv:
```sh
uv pip install -r requirements.txt
```

### 4. Set up your API key
- Copy `.env.example` to `.env` and add your Google Gemini API key:
  ```
  GOOGLE_API_KEY=your_actual_gemini_api_key
  ```

### 5. Add your student PDF and/or DOCX files
- Place all files to be reviewed in the project directory.

### 6. Run the script
```sh
python main.py
```
Or, with uv:
```sh
uv run main.py
```

### 7. View results
- The results will be saved in `results.json` in structured format.

## Notes
- The script uses the Gemini API to review and score each essay.
- The prompt and output structure can be customized in the `.rules` file.
- Make sure your `.env` file is **not** committed to git (it's already in `.gitignore`).

## Troubleshooting
- If you see errors about missing dependencies, make sure your virtual environment is activated and all requirements are installed.
- If you see errors about the API key, double-check your `.env` file and that the key is valid.
- For best results, use Python 3.10 or 3.11.

---

Feel free to open issues or PRs for improvements!
