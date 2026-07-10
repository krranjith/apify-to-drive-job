#fetch_links.py

"""
fetch-job-links (Python). Reads source-docs/Links.csv (URL,STATUS), fetches only blank-Status
URLs from ATS pages via requests, extracts the JD from embedded JSON-LD JobPosting (HTML-region
fallback when absent), and writes Link_jobs.csv (jobs.csv schema). Misses -> FETCH_FAILED.
Archives prior outputs, then flips fetched URLs to Processed in Links.csv. No LinkedIn source.
"""
from __future__ import annotations
import csv
import datetime as dt
import json
import re
import shutil

import requests
from bs4 import BeautifulSoup

import config

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
OUT_COLS = config.CSV_COLUMNS  # jobId,title,companyName,url,applyUrl,skills_required,jobDescription,Status

# Small, defensible skill vocabulary mirrored from jobs.csv granularity.
SKILL_VOCAB = [
    "python", "sql", "r", "snowflake", "dbt", "spark", "hadoop", "aws", "gcp", "azure",
    "databricks", "power bi", "tableau", "looker", "excel", "sas", "scikit-learn",
    "pytorch", "tensorflow", "xgboost", "lightgbm", "causal inference", "forecasting",
    "a/b testing", "experimentation", "statistics", "machine learning", "deep learning",
    "nlp", "llm", "rag", "genai", "langchain", "docker", "kubernetes", "airflow",
    "etl", "pricing", "segmentation", "clustering", "regression", "bigquery",
]


def _now_id(used: set[str]) -> str:
    base = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    val = int(base)
    while str(val) in used:
        val += 1
    used.add(str(val))
    return str(val)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    for bad in ("�", "ï¿½"):
        text = text.replace(bad, "-")
    return text


def _skills_from_jd(jd: str) -> str:
    low = jd.lower()
    found = sorted({s for s in SKILL_VOCAB if s in low})
    return ", ".join(found)


def _parse_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and "@graph" in data:
            candidates = data["@graph"]
        for c in candidates:
            if isinstance(c, dict) and c.get("@type") == "JobPosting":
                title = _clean(c.get("title", ""))
                org = c.get("hiringOrganization", {})
                company = _clean(org.get("name", "")) if isinstance(org, dict) else ""
                desc_html = c.get("description", "")
                desc = _clean(BeautifulSoup(desc_html, "html.parser").get_text(" "))
                if desc:
                    return title, company, desc
    return None


def _parse_html_region(soup: BeautifulSoup):
    """Fallback: strip chrome, take the largest plausible content block."""
    for sel in ("nav", "header", "footer", "script", "style", "form"):
        for t in soup.find_all(sel):
            t.decompose()
    main = soup.find("main") or soup.find("article") or soup.body
    if not main:
        return None
    text = _clean(main.get_text(" "))
    if len(text) < 200:
        return None
    title = _clean(soup.title.get_text()) if soup.title else ""
    return title, "", text


def _fetch(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  fetch error: {e}")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    return _parse_jsonld(soup) or _parse_html_region(soup)


def _read_links_csv() -> list[dict]:
    if not config.LINKS_CSV.exists():
        raise FileNotFoundError(f"Missing {config.LINKS_CSV}. Place Links.csv (URL,STATUS) there.")
    text = config.LINKS_CSV.read_text(encoding="utf-8", errors="replace")
    reader = csv.DictReader(text.splitlines())
    lower = {c.lower(): c for c in (reader.fieldnames or [])}
    url_col = lower.get("url")
    st_col = lower.get("status")
    rows = []
    for i, r in enumerate(reader):
        url = (r.get(url_col, "") if url_col else "").strip()
        status = (r.get(st_col, "") if st_col else "").strip()
        rows.append({"idx": i, "url": url, "status": status})
    return rows


def _archive(path):
    if path.exists():
        config.ARCHIVE.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        shutil.copy2(str(path), str(config.ARCHIVE / f"{path.stem}_{ts}{path.suffix}"))


def fetch_jobs_from_urls(rows: list[dict]) -> list[dict]:
    """
    In-memory counterpart to run(): takes a list of dicts each with a "url" key (e.g. rows
    parsed from a googlesearch Drive sheet) and returns Jobs-schema dicts
    (jobId,title,companyName,url,applyUrl,skills_required,jobDescription,Status), with
    Status="FETCH_FAILED" for misses. No file I/O — used by drive_pipeline.run_links_pipeline.
    """
    seen, todo = set(), []
    for r in rows:
        url = (r.get("url") or "").strip()
        if url.startswith("http") and url not in seen:
            seen.add(url)
            todo.append(url)
    print(f"[fetch] to_fetch={len(todo)} (from {len(rows)} row(s))")

    used_ids: set[str] = set()
    out_rows: list[dict] = []
    for url in todo:
        print(f"  fetching {url[:80]}")
        parsed = _fetch(url)
        jid = _now_id(used_ids)
        if not parsed or not parsed[2]:
            out_rows.append({"jobId": jid, "title": "", "companyName": "", "url": url,
                             "applyUrl": url, "skills_required": "", "jobDescription": "",
                             "Status": "FETCH_FAILED"})
            print("    -> FETCH_FAILED")
            continue
        title, company, jd = parsed
        out_rows.append({"jobId": jid, "title": title, "companyName": company, "url": url,
                         "applyUrl": url, "skills_required": _skills_from_jd(jd),
                         "jobDescription": jd, "Status": ""})
        print(f"    -> OK  {company[:30]} | {title[:40]}")

    ok = sum(1 for r in out_rows if r["Status"] != "FETCH_FAILED")
    print(f"[fetch] OK={ok} FETCH_FAILED={len(out_rows) - ok}")
    return out_rows


def run() -> None:
    rows = _read_links_csv()
    scope = [r for r in rows if r["url"].startswith("http")
             and r["status"].strip().lower() in ("", "nan", "null")]
    # dedup preserving order
    seen, todo = set(), []
    for r in scope:
        if r["url"] not in seen:
            seen.add(r["url"])
            todo.append(r)
    print(f"[fetch] sheet={len(rows)} already_processed={len(rows)-len(scope)} to_fetch={len(todo)}")

    used_ids: set[str] = set()
    out_rows, ok_urls = [], set()
    for r in todo:
        url = r["url"]
        print(f"  fetching {url[:80]}")
        parsed = _fetch(url)
        jid = _now_id(used_ids)
        if not parsed or not parsed[2]:
            out_rows.append({"jobId": jid, "title": "", "companyName": "", "url": url,
                             "applyUrl": url, "skills_required": "", "jobDescription": "",
                             "Status": "FETCH_FAILED"})
            print("    -> FETCH_FAILED")
            continue
        title, company, jd = parsed
        out_rows.append({"jobId": jid, "title": title, "companyName": company, "url": url,
                         "applyUrl": url, "skills_required": _skills_from_jd(jd),
                         "jobDescription": jd, "Status": ""})
        ok_urls.add(url)
        print(f"    -> OK  {company[:30]} | {title[:40]}")

    # Write Link_jobs.csv (archive first).
    _archive(config.LINK_JOBS_CSV)
    with config.LINK_JOBS_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_COLS)
        w.writeheader()
        w.writerows(out_rows)

    # Write Processed back into Links.csv for successfully fetched URLs (archive first).
    _archive(config.LINKS_CSV)
    flipped = 0
    text = config.LINKS_CSV.read_text(encoding="utf-8", errors="replace")
    reader = list(csv.reader(text.splitlines()))
    header = reader[0]
    lower = {c.lower(): idx for idx, c in enumerate(header)}
    u_i = lower.get("url", 0)
    if "status" in lower:
        s_i = lower["status"]
    else:
        header.append("STATUS")
        s_i = len(header) - 1
    new_rows = [header]
    for row in reader[1:]:
        while len(row) <= s_i:
            row.append("")
        if row[u_i].strip() in ok_urls and row[s_i].strip() == "":
            row[s_i] = "Processed"
            flipped += 1
        new_rows.append(row)
    with config.LINKS_CSV.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(new_rows)

    fetched_ok = len(ok_urls)
    failed = len(out_rows) - fetched_ok
    print(f"[fetch] OK={fetched_ok} FETCH_FAILED={failed} flipped_to_Processed={flipped}")
    print(f"[fetch] wrote {config.LINK_JOBS_CSV}")


if __name__ == "__main__":
    run()
