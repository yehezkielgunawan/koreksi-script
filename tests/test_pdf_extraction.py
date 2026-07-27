import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


PROJECT_ROOT = Path(__file__).parents[1]


def _load_script(script_name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    if script_name == "main.py":
        openai_stub = ModuleType("openai")
        setattr(openai_stub, "OpenAI", lambda **_kwargs: object())
        monkeypatch.setitem(sys.modules, "openai", openai_stub)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    else:
        google_stub = ModuleType("google")
        google_stub.__path__ = []
        generativeai_stub = ModuleType("google.generativeai")
        setattr(generativeai_stub, "configure", lambda **_kwargs: None)
        setattr(google_stub, "generativeai", generativeai_stub)
        monkeypatch.setitem(sys.modules, "google", google_stub)
        monkeypatch.setitem(sys.modules, "google.generativeai", generativeai_stub)
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    module_name = f"_test_{Path(script_name).stem}"
    spec = importlib.util.spec_from_file_location(module_name, PROJECT_ROOT / script_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script_name", ["main.py", "group_review.py"])
def test_production_pdf_extractors_handle_synthetic_pages(
    script_name: str,
    synthetic_pdf_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script(script_name, monkeypatch)
    extract_text_from_pdf = module.extract_text_from_pdf

    assert "Jawaban normal" in extract_text_from_pdf(synthetic_pdf_files["text"])

    table_text = extract_text_from_pdf(synthetic_pdf_files["table"])
    assert "Pertanyaan" in table_text
    assert "Jawaban tabel" in table_text

    assert extract_text_from_pdf(synthetic_pdf_files["image_only"]).strip() == ""
    assert extract_text_from_pdf(synthetic_pdf_files["blank_scan"]).strip() == ""
