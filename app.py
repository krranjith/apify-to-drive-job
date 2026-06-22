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
    build_valig_actor_input,
    fetch_dataset_from_run,
    fetch_seen_job_ids,
    insert_job_records,
    normalize_jobs,
    run_apify_actor,
    upload_xlsx_to_drive,
)

# ---------------------------------------------------------------------------
# Actor registry — add new actors here
# ---------------------------------------------------------------------------

ACTORS = {
    "curious_coder/linkedin-jobs-scraper": "LinkedIn Jobs Scraper (curious_coder)",
    "valig/linkedin-jobs-scraper":         "LinkedIn Jobs Scraper (valig)",
}

# ---------------------------------------------------------------------------
# Filter mappings
# ---------------------------------------------------------------------------

DATE_POSTED_OPTIONS = {
    "Past 24 hours": "r86400",
    "Past week":     "r604800",
    "Past month":    "r2592000",
    "Any time":      "",
}

CONTRACT_TYPE_OPTIONS = {
    "Full-time":  "F",
    "Part-time":  "P",
    "Contract":   "C",
    "Temporary":  "T",
    "Internship": "I",
    "Volunteer":  "V",
    "Other":      "O",
}

EXPERIENCE_LEVEL_OPTIONS = {
    "Internship":      "1",
    "Entry level":     "2",
    "Associate":       "3",
    "Mid-Senior level": "4",
    "Director":        "5",
    "Executive":       "6",
}

REMOTE_OPTIONS = {
    "Select...": "",
    "On-site":   "1",
    "Remote":    "2",
    "Hybrid":    "3",
}

# ---------------------------------------------------------------------------
# Dynamic list helper
# ---------------------------------------------------------------------------

def _render_dynamic_list(label: str, key: str) -> list[str]:
    """Render a labelled list of text inputs with Add / Bulk edit / Remove empty."""
    if f"num_{key}" not in st.session_state:
        st.session_state[f"num_{key}"] = 1
    if f"bulk_{key}" not in st.session_state:
        st.session_state[f"bulk_{key}"] = False

    st.markdown(f"**{label}**")
    num = st.session_state[f"num_{key}"]

    if st.session_state[f"bulk_{key}"]:
        current = "\n".join(st.session_state.get(f"{key}_{i}", "") for i in range(num))
        bulk_text = st.text_area(
            "One per line", value=current, key=f"{key}_bulk_text",
            label_visibility="collapsed",
        )
        c1, c2 = st.columns(2)
        if c1.button("Apply", key=f"apply_{key}"):
            vals = [v.strip() for v in bulk_text.split("\n") if v.strip()]
            st.session_state[f"num_{key}"] = max(1, len(vals))
            for i, v in enumerate(vals):
                st.session_state[f"{key}_{i}"] = v
            st.session_state[f"bulk_{key}"] = False
            st.rerun()
        if c2.button("Cancel", key=f"cancel_{key}"):
            st.session_state[f"bulk_{key}"] = False
            st.rerun()
        return [st.session_state.get(f"{key}_{i}", "") for i in range(num)]

    for i in range(num):
        st.text_input(label, key=f"{key}_{i}", label_visibility="collapsed")

    c1, c2, c3 = st.columns(3)
    if c1.button("Add", key=f"add_{key}"):
        st.session_state[f"num_{key}"] += 1
        st.rerun()
    if c2.button("Bulk edit", key=f"bulk_btn_{key}"):
        st.session_state[f"bulk_{key}"] = True
        st.rerun()
    if c3.button("Remove empty fields", key=f"rem_{key}"):
        vals = [st.session_state.get(f"{key}_{i}", "") for i in range(num)]
        vals = [v for v in vals if v.strip()]
        for i in range(num):
            st.session_state.pop(f"{key}_{i}", None)
        st.session_state[f"num_{key}"] = max(1, len(vals))
        for i, v in enumerate(vals):
            st.session_state[f"{key}_{i}"] = v
        st.rerun()

    return [st.session_state.get(f"{key}_{i}", "") for i in range(num)]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

st.title("Apify → Google Drive")

# Actor selector
selected_actor_id = st.selectbox(
    "Actor",
    options=list(ACTORS.keys()),
    format_func=lambda k: ACTORS[k],
)

st.divider()

# ---------------------------------------------------------------------------
# Actor-specific input forms
# ---------------------------------------------------------------------------

actor_input: dict = {}

# ── curious_coder/linkedin-jobs-scraper ─────────────────────────────────────
if selected_actor_id == "curious_coder/linkedin-jobs-scraper":

    col1, col2 = st.columns(2)
    role     = col1.text_input("Job title", value="Data Scientist")
    location = col2.text_input("Job location", value="San Francisco Bay Area")

    col3, col4 = st.columns(2)
    date_posted_label = col3.selectbox("Date posted", list(DATE_POSTED_OPTIONS.keys()), index=0)
    count             = col4.number_input("Results limit", value=100, min_value=1, max_value=500, step=10)

    company_names = _render_dynamic_list("Company name", "company_name")
    company_ids   = _render_dynamic_list("Company id",   "company_id")

    selected_contracts = st.multiselect(
        "Contract type",
        list(CONTRACT_TYPE_OPTIONS.keys()),
        default=["Full-time"],
    )

    selected_exp = st.multiselect(
        "Experience level",
        list(EXPERIENCE_LEVEL_OPTIONS.keys()),
        default=["Mid-Senior level", "Entry level"],
    )

    remote_label = st.selectbox("Remote", list(REMOTE_OPTIONS.keys()), index=0)

    actor_input = build_linkedin_actor_input(
        role=role,
        location=location,
        count=int(count),
        date_posted=DATE_POSTED_OPTIONS[date_posted_label],
        company_ids=[cid.strip() for cid in company_ids if cid.strip()],
        contract_types=[CONTRACT_TYPE_OPTIONS[c] for c in selected_contracts],
        experience_levels=[EXPERIENCE_LEVEL_OPTIONS[e] for e in selected_exp],
        remote=REMOTE_OPTIONS[remote_label],
    )

# ── valig/linkedin-jobs-scraper ──────────────────────────────────────────────
elif selected_actor_id == "valig/linkedin-jobs-scraper":

    col1, col2 = st.columns(2)
    role     = col1.text_input("Job title", value="Data Scientist", key="valig_role")
    location = col2.text_input("Job location", value="San Francisco Bay Area", key="valig_location")

    col3, col4 = st.columns(2)
    date_posted_label = col3.selectbox("Date posted", list(DATE_POSTED_OPTIONS.keys()), index=0, key="valig_date")
    count             = col4.number_input("Results limit", value=100, min_value=1, max_value=500, step=10, key="valig_count")

    valig_company_names = _render_dynamic_list("Company name", "valig_company_name")
    valig_company_ids   = _render_dynamic_list("Company id",   "valig_company_id")

    selected_contracts = st.multiselect(
        "Contract type",
        list(CONTRACT_TYPE_OPTIONS.keys()),
        default=["Full-time"],
        key="valig_contracts",
    )

    selected_exp = st.multiselect(
        "Experience level",
        list(EXPERIENCE_LEVEL_OPTIONS.keys()),
        default=["Mid-Senior level", "Entry level"],
        key="valig_exp",
    )

    actor_input = build_valig_actor_input(
        role=role,
        location=location,
        count=int(count),
        date_posted=DATE_POSTED_OPTIONS[date_posted_label],
        contract_types=[CONTRACT_TYPE_OPTIONS[c] for c in selected_contracts],
        experience_levels=[EXPERIENCE_LEVEL_OPTIONS[e] for e in selected_exp],
        company_names=[n.strip() for n in valig_company_names if n.strip()],
        company_ids=[cid.strip() for cid in valig_company_ids if cid.strip()],
    )

run_id = st.text_input(
    "Existing Apify Run ID (leave blank to start a fresh scrape)", value=""
)

# Live URL / input preview
if not run_id.strip() and actor_input:
    with st.expander("Generated actor input", expanded=True):
        if selected_actor_id == "curious_coder/linkedin-jobs-scraper":
            st.code(actor_input["urls"][0], language=None)
        else:
            import json as _json
            st.code(_json.dumps(actor_input, indent=2), language="json")

st.divider()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if st.button("Run", type="primary"):
    log_box   = st.empty()
    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        log_box.code("\n".join(log_lines), language=None)

    try:
        apify_token = os.environ.get("APIFY_API_TOKEN", "").strip()

        if not apify_token:
            st.error("APIFY_API_TOKEN not found in .env")
            st.stop()

        # Step 1 — scrape
        if run_id.strip():
            log(f"Fetching existing run {run_id.strip()} …")
            raw_items, apify_run_id = fetch_dataset_from_run(run_id.strip(), apify_token)
        else:
            log(f"Starting fresh scrape with actor '{selected_actor_id}' …")
            raw_items, apify_run_id = run_apify_actor(actor_input, selected_actor_id, apify_token)

        log(f"Downloaded {len(raw_items)} raw items.")

        # Step 2 — normalize
        cleaned = normalize_jobs(raw_items)
        log(f"Normalized → {len(cleaned)} jobs.")

        # Step 3 — deduplicate via Supabase
        log("Fetching seen job IDs from Supabase …")
        seen_ids   = fetch_seen_job_ids()
        seen_in_batch: set[str] = set()
        new_jobs: list = []
        for j in cleaned:
            if j.job_id not in seen_ids and j.job_id not in seen_in_batch:
                new_jobs.append(j)
                seen_in_batch.add(j.job_id)
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
                    "title":  j.title,
                    "company": j.company,
                    "link":   j.url,
                    "run_id": apify_run_id,
                    "status": "new",
                    "relevantDescription": j.description,
                }
                for j in new_jobs
            ]
            insert_job_records(records)
            log("Supabase updated.")

        log("All steps complete.")

        st.success("Run complete!")
        c1, c2 = st.columns(2)
        c1.metric("Duplicates skipped", duplicates)
        c2.metric("New jobs uploaded", len(new_jobs))

    except Exception as exc:
        st.error(f"Error: {exc}")
