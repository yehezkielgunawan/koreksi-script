from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle


def _create_text_pdf(path: Path) -> None:
    pdf = Canvas(str(path), pagesize=A4)
    pdf.drawString(72, 770, "Jawaban normal untuk pengujian ekstraksi.")
    pdf.showPage()
    pdf.save()


def _create_table_pdf(path: Path) -> None:
    pdf = Canvas(str(path), pagesize=A4)
    table = Table(
        [["Pertanyaan", "Jawaban"], ["1", "Jawaban tabel"]],
        colWidths=[140, 280],
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    table.wrapOn(pdf, 420, 700)
    table.drawOn(pdf, 72, 650)
    pdf.showPage()
    pdf.save()


def _create_image_only_pdf(path: Path, image_path: Path) -> None:
    pdf = Canvas(str(path), pagesize=A4)
    pdf.drawImage(str(image_path), 48, 550, width=500, height=150)
    pdf.showPage()
    pdf.save()


def _create_blank_scan_pdf(path: Path, tmp_path: Path) -> None:
    scan_path = tmp_path / "blank_scan_source.png"
    scan = Image.new("RGB", (1000, 1400), (246, 246, 242))
    draw = ImageDraw.Draw(scan)
    draw.rectangle((15, 15, 984, 1384), outline=(220, 220, 215), width=4)
    scan.save(scan_path, "PNG")

    pdf = Canvas(str(path), pagesize=A4)
    pdf.drawImage(str(scan_path), 0, 0, width=A4[0], height=A4[1])
    pdf.showPage()
    pdf.save()
    scan_path.unlink()


@pytest.fixture
def synthetic_pdf_files(tmp_path: Path) -> dict[str, Path]:
    answer_image = tmp_path / "answer.png"
    image = Image.new("RGB", (1000, 300), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=36)
    draw.text(
        (50, 115),
        "Jawaban pada gambar untuk pengujian OCR.",
        fill="black",
        font=font,
    )
    image.save(answer_image, "PNG")

    samples = {
        "text": tmp_path / "text.pdf",
        "table": tmp_path / "table.pdf",
        "image_only": tmp_path / "image_only.pdf",
        "blank_scan": tmp_path / "blank_scan.pdf",
        "answer_image": answer_image,
    }
    _create_text_pdf(samples["text"])
    _create_table_pdf(samples["table"])
    _create_image_only_pdf(samples["image_only"], answer_image)
    _create_blank_scan_pdf(samples["blank_scan"], tmp_path)
    return samples
