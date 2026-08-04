from pathlib import Path

import pytest
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


@pytest.fixture
def synthetic_pdf_files(tmp_path: Path) -> dict[str, Path]:
    samples = {
        "text": tmp_path / "text.pdf",
        "table": tmp_path / "table.pdf",
    }
    _create_text_pdf(samples["text"])
    _create_table_pdf(samples["table"])
    return samples
