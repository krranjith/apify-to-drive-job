"""
Streamlit UI for the Apify → Google Drive pipeline.
Run: streamlit run app.py
"""

import os
import re
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / ".env")

from run_apify_to_drive import (
    build_linkedin_actor_input,
    fetch_dataset_from_run,
    fetch_seen_job_ids,
    insert_job_records,
    normalize_jobs,
    run_apify_actor,
    upload_xlsx_to_drive,
)

st.title("Apify → Google Drive")

with st.form("run_form"):
    role     = st.text_input("Role", value="Data Scientist")
    location = st.text_input("Location", value="San Jose")
    count    = st.number_input("Max results", value=100, min_value=1, max_value=500, step=10)
    run_id   = st.text_input("Existing Apify Run ID (leave blank to start a fresh scrape)", value="")
    submitted = st.form_submit_button("Run")

if submitted:
    log_box   = st.empty()
    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        log_box.code("\n".join(log_lines), language=None)

    try:
        apify_token = os.environ.get("APIFY_API_TOKEN", "").strip()
        actor_id    = os.environ.get("APIFY_ACTOR_ID", "curious_coder/linkedin-jobs-scraper").strip()

        if not apify_token:
            st.error("APIFY_API_TOKEN not found in .env")
            st.stop()

        # Step 1 — scrape
        if run_id.strip():
            log(f"Fetching existing run {run_id.strip()} …")
            raw_items, apify_run_id = fetch_dataset_from_run(run_id.strip(), apify_token)
        else:
            log(f"Starting fresh scrape: '{role}' in '{location}' (count={int(count)}) …")
            actor_input = build_linkedin_actor_input(role, location, int(count))
            raw_items, apify_run_id = run_apify_actor(actor_input, actor_id, apify_token)

        log(f"Downloaded {len(raw_items)} raw items.")

        # Step 2 — normalize
        cleaned = normalize_jobs(raw_items)
        log(f"Normalized → {len(cleaned)} jobs.")

        # Step 3 — deduplicate via Supabase
        log("Fetching seen job IDs from Supabase …")
        seen_ids  = fetch_seen_job_ids()
        new_jobs  = [j for j in cleaned if j.job_id not in seen_ids]
        duplicates = len(cleaned) - len(new_jobs)
        log(f"Duplicates skipped: {duplicates}   New jobs: {len(new_jobs)}")

        if not new_jobs:
            st.warning("No new jobs — nothing to upload.")
        else:
            # Step 4 — upload XLSX
            folder_id    = os.environ.get("GOOGLE_DRIVE_ANALYSIS_FOLDER_ID", "").strip()
            source_label = re.sub(r"[^a-z0-9]+", "_", role.lower()).strip("_")
            log(f"Uploading XLSX to Drive folder {folder_id} …")
            xlsx_id, xlsx_name = upload_xlsx_to_drive(new_jobs, folder_id, f"jobs_{source_label}")
            log(f"Uploaded: {xlsx_name}  (id={xlsx_id})")

            # Step 5 — record in Supabase
            records = [
                {
                    "job_id": int(j.job_id),
                    "title": j.title,
                    "company": j.company,
                    "link": j.url,
                    "run_id": apify_run_id,
                    "status": "new",
                    "relevantDescription": j.description,
                }
                for j in new_jobs
            ]
            insert_job_records(records)
            log("Supabase updated.")

        log("All steps complete.")

        # Summary
        st.success("Run complete!")
        c1, c2 = st.columns(2)
        c1.metric("Duplicates skipped", duplicates)
        c2.metric("New jobs uploaded", len(new_jobs))

    except Exception as exc:
        st.error(f"Error: {exc}")
