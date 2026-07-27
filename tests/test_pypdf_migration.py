import ast
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parents[1]
LEGACY_PACKAGE = "PyPDF" + "2"


@pytest.mark.parametrize("script_name", ["main.py", "group_review.py"])
def test_scripts_use_maintained_pdf_package(script_name: str) -> None:
    tree = ast.parse((PROJECT_ROOT / script_name).read_text(encoding="utf-8"))
    legacy_lines = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name.split(".", 1)[0] == LEGACY_PACKAGE for alias in node.names
        ):
            legacy_lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module and (
            node.module.split(".", 1)[0] == LEGACY_PACKAGE
        ):
            legacy_lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == LEGACY_PACKAGE:
            legacy_lines.append(node.lineno)

    assert not legacy_lines, (
        f"{script_name} references {LEGACY_PACKAGE} on lines {legacy_lines}"
    )
