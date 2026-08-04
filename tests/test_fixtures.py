from pathlib import Path

from pypdf import PdfReader


def test_synthetic_pdf_files_create_reusable_extraction_samples(
    synthetic_pdf_files: dict[str, Path],
) -> None:
    expected_names = {
        "text": "text.pdf",
        "table": "table.pdf",
    }

    assert set(synthetic_pdf_files) == set(expected_names)
    for sample, filename in expected_names.items():
        path = synthetic_pdf_files[sample]
        assert path.name == filename
        assert path.is_file()
        assert path.stat().st_size > 0

    text_reader = PdfReader(synthetic_pdf_files["text"])
    table_reader = PdfReader(synthetic_pdf_files["table"])

    assert len(text_reader.pages) == 1
    assert "Jawaban normal" in text_reader.pages[0].extract_text()
    assert len(table_reader.pages) == 1
    assert "Pertanyaan" in table_reader.pages[0].extract_text()
    assert "Jawaban tabel" in table_reader.pages[0].extract_text()
