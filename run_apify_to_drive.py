"""
Standalone script: Apify scrape → clean → deduplicate via Supabase → upload to Google Drive.

Usage:
    python run_apify_to_drive.py
    python run_apify_to_drive.py --role "Data Scientist" --location "New York" --count 50
    python run_apify_to_drive.py --dataset-id <existing_dataset_id>

Reads credentials from .env in the same directory. Fully self-contained —
no imports from the existing job_agent package.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
import requests
from dotenv import load_dotenv
from supabase import create_client
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------------------------------------------------------------------------
# Config — loaded from .env
# ---------------------------------------------------------------------------

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

# ---------------------------------------------------------------------------
# Search configuration — edit these to change what gets scraped
# ---------------------------------------------------------------------------
SEARCH_ROLE     = "Data Scientist"
SEARCH_LOCATION = "San Jose"
SEARCH_COUNT    = 100

# If RUN_ID is set, fetch results from that existing Apify run instead of
# starting a new one. Set to "" to always trigger a fresh scrape.
RUN_ID = "DDbTLRoc80iU6DHdw"


def _require_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        print(f"ERROR: {key} is not set in {ENV_FILE}")
        sys.exit(1)
    return value


# ---------------------------------------------------------------------------
# Supabase (self-contained — mirrors job_agent/supabase_client.py)
# ---------------------------------------------------------------------------

def _supabase_client():
    url = _require_env("SUPABASE_URL")
    # Use service role key to bypass RLS; fall back to anon key if not set
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip() or _require_env("SUPABASE_KEY")
    return create_client(url, key)


def fetch_seen_job_ids() -> set[str]:
    table = os.environ.get("SUPABASE_TABLE", "jobs")
    client = _supabase_client()
    ids: set[str] = set()
    page_size = 1000
    offset = 0
    try:
        while True:
            response = client.table(table).select("job_id").range(offset, offset + page_size - 1).execute()
            rows = response.data or []
            ids.update(str(row.get("job_id")) for row in rows if row.get("job_id") is not None)
            if len(rows) < page_size:
                break
            offset += page_size
        print(f"  -> {len(ids)} existing job ID(s) fetched from Supabase")
        return ids
    except Exception as exc:
        print(f"  [Supabase] fetch failed: {exc}")
        return set()


def insert_job_records(records: list[dict]) -> None:
    if not records:
        return
    table = os.environ.get("SUPABASE_TABLE", "jobs")
    try:
        _supabase_client().table(table).upsert(records, on_conflict="job_id").execute()
        print(f"  ->{len(records)} record(s) upserted into Supabase")
    except Exception as exc:
        print(f"  [Supabase] insert failed: {exc}")


# ---------------------------------------------------------------------------
# Text utilities (self-contained copies from text_utils.py)
# ---------------------------------------------------------------------------

KNOWN_SKILLS = [
    "python", "sql", "tableau", "power bi", "snowflake", "spark",
    "machine learning", "forecasting", "experimentation", "statistics",
    "pandas", "scikit-learn", "dataiku", "pricing", "analytics",
    "strategy", "dashboard", "powerpoint",
]


def sanitize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def detect_skills(text: str) -> list[str]:
    haystack = sanitize_text(text).lower()
    return sorted(skill for skill in KNOWN_SKILLS if skill in haystack)


def extract_years_required(text: str) -> tuple[int | None, int | None]:
    lowered = sanitize_text(text).lower()
    ranges = re.findall(r"(\d+)\s*(?:\+|to|-|–)\s*(\d+)\s+years", lowered)
    if ranges:
        low, high = ranges[0]
        return int(low), int(high)
    single = re.findall(r"(\d+)\+?\s+years", lowered)
    if single:
        return int(single[0]), None
    return None, None


def classify_remote_mode(location: str, description: str) -> str:
    combined = f"{location} {description}".lower()
    if "remote" in combined:
        return "remote"
    if "hybrid" in combined:
        return "hybrid"
    if "onsite" in combined or "on-site" in combined:
        return "onsite"
    return "unknown"


def _infer_seniority(title: str, description: str) -> str:
    combined = f"{title} {description}".lower()
    if "senior" in combined or "lead" in combined or "staff" in combined:
        return "senior"
    if "principal" in combined or "director" in combined:
        return "executive"
    if "intern" in combined or "entry" in combined or "junior" in combined:
        return "entry"
    return "mid"


def _pick(record: dict, *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value:
            return str(value)
    return ""


# ---------------------------------------------------------------------------
# Job normalization
# ---------------------------------------------------------------------------

@dataclass
class CleanedJob:
    job_id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    salary_text: str
    remote_mode: str
    seniority: str
    skills_required: list[str]
    min_years_required: int | None
    max_years_required: int | None
    requires_visa_support: bool
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw")  # exclude raw payload to keep output lean
        return d


def normalize_jobs(raw_jobs: list[dict]) -> list[CleanedJob]:
    jobs: list[CleanedJob] = []
    for record in raw_jobs:
        title = sanitize_text(_pick(record, "title", "positionName", "jobTitle"))
        company = sanitize_text(_pick(record, "company", "companyName", "company_name"))
        location = sanitize_text(_pick(record, "location", "jobLocation", "formattedLocation"))
        description = sanitize_text(
            _pick(record, "description", "descriptionText", "jobDescription", "body", "text")
        )
        url = sanitize_text(_pick(record, "url", "jobUrl", "applyUrl", "link"))
        salary_text = sanitize_text(_pick(record, "salary", "salaryRange", "compensation"))
        requires_visa = any(
            kw in description.lower()
            for kw in ("sponsorship", "visa", "work authorization")
        )
        min_yrs, max_yrs = extract_years_required(f"{title} {description}")
        raw_id = record.get("jobId") or record.get("id") or record.get("linkedinJobId")
        if raw_id:
            job_id = str(raw_id)
        else:
            job_id = str(int(hashlib.sha1(
                "|".join([title, company, location, url]).encode()
            ).hexdigest()[:15], 16))
        jobs.append(CleanedJob(
            job_id=job_id,
            title=title or "Unknown role",
            company=company or "Unknown company",
            location=location or "Unknown location",
            description=description,
            url=url,
            salary_text=salary_text,
            remote_mode=classify_remote_mode(location, description),
            seniority=_infer_seniority(title, description),
            skills_required=detect_skills(description),
            min_years_required=min_yrs,
            max_years_required=max_yrs,
            requires_visa_support=requires_visa,
            raw=record,
        ))
    return jobs


# ---------------------------------------------------------------------------
# Apify
# ---------------------------------------------------------------------------

def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _normalize_actor_id(actor_id: str) -> str:
    actor_id = actor_id.strip()
    if "/" in actor_id and "~" not in actor_id:
        owner, name = actor_id.split("/", 1)
        return f"{owner}~{name}"
    return actor_id


def _download_dataset(dataset_id: str, token: str) -> list[dict]:
    resp = requests.get(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
        params={"clean": "true", "format": "json"},
        headers=_auth_headers(token),
        timeout=120,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise ValueError("Apify dataset response was not a JSON list")
    return payload


def fetch_dataset_from_run(run_id: str, token: str) -> tuple[list[dict], str]:
    print(f"Fetching dataset from existing Apify run {run_id} …")
    resp = requests.get(
        f"https://api.apify.com/v2/actor-runs/{run_id}",
        headers=_auth_headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    run_data = resp.json().get("data", {})
    dataset_id = run_data.get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError(f"Could not find defaultDatasetId for run {run_id}")
    print(f"  ->resolved to dataset {dataset_id}")
    items = _download_dataset(dataset_id, token)
    print(f"  ->{len(items)} items downloaded")
    return items, run_id


def run_apify_actor(actor_input: dict, actor_id: str, token: str) -> tuple[list[dict], str]:
    actor_id = _normalize_actor_id(actor_id)
    print(f"Starting Apify actor {actor_id} …")
    resp = requests.post(
        f"https://api.apify.com/v2/acts/{actor_id}/runs",
        params={"waitForFinish": 10},
        headers=_auth_headers(token),
        json=actor_input,
        timeout=240,
    )
    resp.raise_for_status()
    run_data = resp.json().get("data", {})
    run_id = run_data.get("id")
    dataset_id = run_data.get("defaultDatasetId")
    if not run_id or not dataset_id:
        raise RuntimeError("Apify did not return run id or defaultDatasetId")

    # Poll until the actor finishes
    status = run_data.get("status")
    poll_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    print(f"  Run {run_id} started (status: {status}). Polling …")
    waited = 0
    while status not in ("SUCCEEDED", "FAILED", "ABORTED") and waited < 600:
        time.sleep(5)
        waited += 5
        poll = requests.get(poll_url, headers=_auth_headers(token), timeout=60)
        poll.raise_for_status()
        status = poll.json().get("data", {}).get("status")
        print(f"  [{waited:>3}s] status: {status}")

    if status != "SUCCEEDED":
        raise RuntimeError(f"Apify actor did not succeed — final status: {status}")

    print(f"Actor finished. Downloading dataset {dataset_id} …")
    items = _download_dataset(dataset_id, token)
    print(f"  ->{len(items)} items downloaded")
    return items, run_id


def build_linkedin_actor_input(role: str, location: str, count: int) -> dict:
    keywords = requests.utils.quote(role)
    loc_encoded = requests.utils.quote(location)
    url = f"https://www.linkedin.com/jobs/search/?keywords={keywords}&location={loc_encoded}&f_TPR=r86400"
    return {"urls": [url], "count": count}


# ---------------------------------------------------------------------------
# Google Drive upload
# ---------------------------------------------------------------------------

def _build_drive_service():
    creds = Credentials(
        token=None,
        refresh_token=_require_env("GOOGLE_OAUTH_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_require_env("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=_require_env("GOOGLE_OAUTH_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds, cache_discovery=False)



def upload_xlsx_to_drive(jobs: list, folder_id: str, name_prefix: str) -> tuple[str, str]:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(["jobId", "jobDescription", "jobLink", "companyName", "Status"])

    for job in jobs:
        apply_url = job.raw.get("applyUrl") or job.url
        ws.append([
            job.job_id,
            job.description,
            apply_url,
            job.company,
            "",
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    service = _build_drive_service()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"{name_prefix}_{timestamp}.xlsx"
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    media = MediaIoBaseUpload(buffer, mimetype=mime, resumable=False)
    result = (
        service.files()
        .create(
            body={"name": file_name, "parents": [folder_id], "mimeType": mime},
            media_body=media,
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )
    return result.get("id", ""), file_name


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    apify_token = _require_env("APIFY_API_TOKEN")
    actor_id = os.environ.get("APIFY_ACTOR_ID", "curious_coder/linkedin-jobs-scraper").strip()


    # Step 1 — scrape (use existing run if RUN_ID is set, otherwise start fresh)
    if RUN_ID:
        raw_items, apify_run_id = fetch_dataset_from_run(RUN_ID, apify_token)
    else:
        actor_input = build_linkedin_actor_input(SEARCH_ROLE, SEARCH_LOCATION, SEARCH_COUNT)
        print(f"Actor input: {json.dumps(actor_input, indent=2)}")
        raw_items, apify_run_id = run_apify_actor(actor_input, actor_id, apify_token)
    source_label = re.sub(r"[^a-z0-9]+", "_", SEARCH_ROLE.lower()).strip("_")

    # Step 2 — clean
    print(f"\nNormalizing {len(raw_items)} raw jobs …")
    cleaned = normalize_jobs(raw_items)
    print(f"  ->{len(cleaned)} jobs normalized")

    # Step 3 — deduplicate against Supabase by job_id
    print("\nFetching seen job IDs from Supabase …")
    seen_ids = fetch_seen_job_ids()
    new_jobs = [job for job in cleaned if job.job_id not in seen_ids]
    skipped = len(cleaned) - len(new_jobs)
    print(f"  ->{skipped} duplicate(s) removed, {len(new_jobs)} new job(s) remaining")

    if not new_jobs:
        print("\nNo new jobs to upload. Exiting.")
        return

    # Step 4 — upload XLSX to analysis folder
    analysis_folder_id = _require_env("GOOGLE_DRIVE_ANALYSIS_FOLDER_ID")
    print(f"\nUploading XLSX to analysis folder {analysis_folder_id} …")
    xlsx_id, xlsx_name = upload_xlsx_to_drive(new_jobs, analysis_folder_id, f"jobs_{source_label}")
    print(f"  Uploaded: {xlsx_name}  (file_id={xlsx_id})")

    # Step 5 — record new job IDs in Supabase
    print(f"\nInserting {len(new_jobs)} job ID(s) into Supabase …")
    records = [
        {
            "job_id": int(job.job_id),
            "title": job.title,
            "company": job.company,
            "link": job.url,
            "run_id": apify_run_id,
            "status": "new",
            "relevantDescription": job.description,
        }
        for job in new_jobs
    ]
    insert_job_records(records)
    print("  Done.")

    print("\nAll steps complete.")


if __name__ == "__main__":
    main()
