from pathlib import Path

from PIL import Image
from pypdf import PdfReader


def test_synthetic_pdf_files_create_reusable_extraction_samples(
    synthetic_pdf_files: dict[str, Path],
) -> None:
    expected_names = {
        "text": "text.pdf",
        "table": "table.pdf",
        "image_only": "image_only.pdf",
        "blank_scan": "blank_scan.pdf",
        "answer_image": "answer.png",
    }

    assert set(synthetic_pdf_files) == set(expected_names)
    for sample, filename in expected_names.items():
        path = synthetic_pdf_files[sample]
        assert path.name == filename
        assert path.is_file()
        assert path.stat().st_size > 0

    text_reader = PdfReader(synthetic_pdf_files["text"])
    table_reader = PdfReader(synthetic_pdf_files["table"])
    image_reader = PdfReader(synthetic_pdf_files["image_only"])
    blank_reader = PdfReader(synthetic_pdf_files["blank_scan"])

    assert len(text_reader.pages) == 1
    assert "Jawaban normal" in text_reader.pages[0].extract_text()
    assert len(table_reader.pages) == 1
    assert "Pertanyaan" in table_reader.pages[0].extract_text()
    assert "Jawaban tabel" in table_reader.pages[0].extract_text()
    assert len(image_reader.pages) == 1
    assert image_reader.pages[0].extract_text().strip() == ""
    assert len(image_reader.pages[0].images) == 1
    assert len(blank_reader.pages) == 1
    assert blank_reader.pages[0].extract_text().strip() == ""
    assert len(blank_reader.pages[0].images) == 1

    with Image.open(synthetic_pdf_files["answer_image"]) as image:
        assert image.format == "PNG"
        assert image.size == (1000, 300)
        assert min(channel[0] for channel in image.getextrema()) < 255
