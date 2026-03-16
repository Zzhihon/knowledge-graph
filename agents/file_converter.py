"""Unified file-to-text converter for knowledge ingestion.

Supports Markdown (.md), plain text (.txt), and PDF (.pdf) files.
PDF conversion uses PyMuPDF (lazy-imported to avoid load penalty).
"""

from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}


def is_supported(file_path: Path) -> bool:
    """Check whether the file extension is supported for ingestion."""
    return file_path.suffix.lower() in SUPPORTED_EXTENSIONS


def convert_to_text(file_path: Path) -> str:
    """Convert a supported file to plain text.

    Args:
        file_path: Path to the source file.

    Returns:
        Extracted text content.

    Raises:
        ValueError: If the file type is unsupported.
        FileNotFoundError: If the file does not exist.
    """
    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = file_path.suffix.lower()
    if ext == ".pdf":
        return _convert_pdf(file_path)
    if ext in (".md", ".txt"):
        return _convert_markdown(file_path)

    raise ValueError(
        f"不支持的文件格式: {ext}. 支持的格式: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
    )


def _convert_markdown(file_path: Path) -> str:
    """Read a markdown or plain text file as UTF-8."""
    return file_path.read_text(encoding="utf-8")


def _convert_pdf(file_path: Path) -> str:
    """Extract text from a PDF using PyMuPDF.

    PyMuPDF is imported lazily to avoid the heavy load cost when
    only processing markdown files.
    """
    import fitz  # pymupdf

    pages: list[str] = []
    with fitz.open(file_path) as doc:
        for page in doc:
            text = page.get_text()
            if text.strip():
                pages.append(text)

    return "\n\n".join(pages)
