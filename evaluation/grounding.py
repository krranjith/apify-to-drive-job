#grounding.py

"""
Grounding loader. The Cowork skills told the model to "Read Ranjith_resume.pdf /
Extended.docx / DATA-CONTRACT.md". The API is stateless with no filesystem, so we read
those files here and inject their text into the system prompt instead. Same content,
same instructions — only the delivery channel changes (an allowed I/O-path change).
"""
from __future__ import annotations
import functools
import json

import config


@functools.lru_cache(maxsize=1)
def data_contract() -> str:
    return config.DATA_CONTRACT.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def canonical_resume_text() -> str:
    """The canonical resume as JSON text — the single source of truth for tailoring."""
    return config.CANONICAL_JSON.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def canonical_resume() -> dict:
    return json.loads(canonical_resume_text())


@functools.lru_cache(maxsize=1)
def resume_pdf_text() -> str:
    """One-pager text. Best-effort: if the PDF is unreadable, fall back to the canonical JSON."""
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(str(config.RESUME_PDF)) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
        if text:
            return text
    except Exception as e:  # noqa: BLE001 - grounding is best-effort by design
        print(f"[grounding] resume PDF unreadable ({e}); using canonical JSON as one-pager.")
    return canonical_resume_text()


@functools.lru_cache(maxsize=1)
def extended_docx_text() -> str:
    """Extended grounding doc. If unavailable, return '' and let callers flag partial grounding."""
    try:
        import docx  # python-docx
        d = docx.Document(str(config.EXTENDED_DOCX))
        return "\n".join(p.text for p in d.paragraphs if p.text.strip())
    except Exception as e:  # noqa: BLE001
        print(f"[grounding] extended docx unavailable ({e}); grounding is PARTIAL (one-pager only).")
        return ""


def grounding_block() -> str:
    """The full grounding context injected into evaluate/tailor system prompts."""
    ext = extended_docx_text()
    ext_section = ext if ext else "(EXTENDED RESUME UNAVAILABLE — grounding is partial; use the one-pager only.)"
    return (
        "=== DATA CONTRACT (output schema you MUST follow exactly) ===\n"
        f"{data_contract()}\n\n"
        "=== ONE-PAGE RESUME (canonical layout/bullets) ===\n"
        f"{resume_pdf_text()}\n\n"
        "=== CANONICAL RESUME JSON (authoritative structure for tailoring) ===\n"
        f"{canonical_resume_text()}\n\n"
        "=== EXTENDED RESUME (full fact base — every score/bullet must trace here or the one-pager) ===\n"
        f"{ext_section}\n"
    )
