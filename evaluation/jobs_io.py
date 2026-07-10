#jobs_io.py

"""
Shared CSV I/O for source job files. Encoding-tolerant read (utf-8 -> cp1252),
mojibake cleanup, schema mapping, and cross-file dedup on applyUrl — identical to the
evaluate-jobs / DATA-CONTRACT rules. Used by evaluate.py and the pipeline write-back.
"""
from __future__ import annotations
import csv
import io
from pathlib import Path

import config


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def clean_mojibake(s: str) -> str:
    if not s:
        return s
    for bad in ("�", "ï¿½"):
        s = s.replace(bad, "-")
    return s.replace("\xa0", " ")


def read_rows(path: Path) -> list[dict]:
    """Read a jobs.csv-schema file into a list of dict rows (values mojibake-cleaned)."""
    if not path.exists():
        return []
    text = _decode(path.read_bytes())
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for r in reader:
        rows.append({k: clean_mojibake((v or "").strip()) for k, v in r.items()})
    return rows


def is_blank_status(status: str) -> bool:
    """A row is in scope for scoring iff its Status is blank/NULL/whitespace."""
    s = (status or "").strip().lower()
    return s in ("", "nan", "null", "none")


def map_to_schema(row: dict, source_file: str) -> dict:
    """CSV header -> evaluations schema field mapping (applyUrl->jobLink; url ignored)."""
    return {
        "jobId": row.get("jobId", ""),
        "companyName": row.get("companyName", ""),
        "jobTitle": row.get("title", ""),
        "jobLink": row.get("applyUrl", ""),      # apply link, NOT url
        "applyUrl": row.get("applyUrl", ""),     # kept for dedup + write-back keying
        "sourceFile": source_file,
        "Status": row.get("Status", ""),          # verbatim input Status
        "skillsRequired": row.get("skills_required", ""),  # passthrough, NOT scored
        "jobDescription": row.get("jobDescription", ""),   # used for scoring only
    }


def build_work_queue() -> tuple[list[dict], dict]:
    """
    Concatenate jobs.csv + Link_jobs.csv (when present), skip non-blank Status rows and
    FETCH_FAILED, dedup on applyUrl (first occurrence wins). Returns (queue, stats).
    """
    queue: list[dict] = []
    seen_urls: set[str] = set()
    stats = {"total": 0, "already_processed": 0, "fetch_failed": 0, "duplicates": 0}

    for path, name in ((config.JOBS_CSV, "jobs.csv"), (config.LINK_JOBS_CSV, "Link_jobs.csv")):
        for row in read_rows(path):
            stats["total"] += 1
            status = row.get("Status", "")
            if status.strip().upper() == "FETCH_FAILED":
                stats["fetch_failed"] += 1
                continue
            if not is_blank_status(status):
                stats["already_processed"] += 1
                continue
            url = row.get("applyUrl", "")
            if url and url in seen_urls:
                stats["duplicates"] += 1
                continue
            if url:
                seen_urls.add(url)
            queue.append(map_to_schema(row, name))
    return queue, stats

