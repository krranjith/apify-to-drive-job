#!/usr/bin/env python3
"""
Standalone script: Google Advanced Job Search (Serper.dev) → upload results as a Google Sheet to Google Drive.

Usage:
    python google_search_to_drive.py
    python google_search_to_drive.py --date w
    python google_search_to_drive.py --date m --num 100
    python google_search_to_drive.py --query "your query"

Reads credentials from .env in the same directory. Fully self-contained —
mirrors the Drive-upload logic in run_apify_to_drive.py.

Date options:  h=hour  d=day  w=week  m=month  y=year
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------------------------------------------------------------------------
# Config — loaded from .env
# ---------------------------------------------------------------------------

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

console = Console()

SERPER_URL = "https://google.serper.dev/search"

# Serper tbs date map
DATE_MAP = {
    "h": "qdr:h",   # past hour
    "d": "qdr:d",   # past 24 hours
    "w": "qdr:w",   # past week
    "m": "qdr:m",   # past month
    "y": "qdr:y",   # past year
}

DEFAULT_QUERY = (
    '(site:greenhouse.io OR site:lever.co OR site:myworkdayjobs.com OR site:icims.com) '
    '(intitle:"data scientist" OR intitle:business) '
    '("United States" OR "USA" OR "Remote") '
    '-intitle:intern -intitle:principal -intitle:director'
)

_ROLE_TITLES = (
    '(intitle:"data scientist" OR intitle:"machine learning engineer" '
    'OR intitle:"applied scientist" OR intitle:"analytics engineer" '
    'OR intitle:"business analyst")'
)

# Preset queries for the UI dropdown — each restricts to a set of company
# career-site domains and searches for role titles within them.
PRESET_QUERIES: dict[str, str] = {
    "Tech Giants": (
        '(site:careers.google.com OR site:metacareers.com OR site:careers.microsoft.com '
        'OR site:jobs.apple.com OR site:amazon.jobs) ' + _ROLE_TITLES
    ),
    "Consumer Tech": (
        '(site:jobs.netflix.com OR site:careers.uber.com OR site:careers.airbnb.com '
        'OR site:careers.lyft.com OR site:careers.x.com) ' + _ROLE_TITLES
    ),
    "AI Labs 1": (
        '(site:openai.com/careers OR site:anthropic.com/careers OR site:x.ai/careers '
        'OR site:cohere.com/careers OR site:mistral.ai/careers) ' + _ROLE_TITLES
    ),
    "AI Labs 2": (
        '(site:scale.com/careers OR site:huggingface.co/jobs OR site:stability.ai/careers '
        'OR site:inflection.ai/careers OR site:careers.perplexity.ai) ' + _ROLE_TITLES
    ),
    "Chip / Hardware": (
        '(site:nvidia.wd5.myworkdayjobs.com OR site:careers.amd.com OR site:intel.wd1.myworkdayjobs.com '
        'OR site:careers.qualcomm.com OR site:waymo.com/careers) ' + _ROLE_TITLES
    ),
    "Cloud / Data Infra": (
        '(site:databricks.com/company/careers OR site:careers.snowflake.com OR site:palantir.com/careers '
        'OR site:careers.datadoghq.com OR site:careers.confluent.io) ' + _ROLE_TITLES
    ),
    "Fintech": (
        '(site:stripe.com/jobs OR site:careers.robinhood.com OR site:careers.bloomberg.com '
        'OR site:careers.twosigma.com OR site:citadel.com/careers) ' + _ROLE_TITLES
    ),
    "Finance / Banking": (
        '(site:careers.jpmorgan.com OR site:goldmansachs.com/careers OR site:americanexpress.com/careers '
        'OR site:capitalonecareers.com OR site:careers.pypl.com) ' + _ROLE_TITLES
    ),
    "Healthcare / Biotech": (
        '(site:jobs.jnj.com OR site:careers.pfizer.com OR site:flatironhealth.com/careers '
        'OR site:tempus.com/careers OR site:recursion.com/careers) ' + _ROLE_TITLES
    ),
    "Automotive / Robotics": (
        '(site:tesla.com/careers OR site:getcruise.com/careers OR site:bostondynamics.com/careers '
        'OR site:aurora.tech/careers OR site:careers.rivian.com) ' + _ROLE_TITLES
    ),
}


def _require_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        print(f"ERROR: {key} is not set in {ENV_FILE}")
        sys.exit(1)
    return value


# ---------------------------------------------------------------------------
# Serper search
# ---------------------------------------------------------------------------

def get_serper_balance(api_key: str) -> int:
    """Fetch remaining Serper credit balance. Does not cost a search credit."""
    resp = requests.get(
        "https://google.serper.dev/account",
        headers={"X-API-KEY": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("balance", 0)


def search_serper(query: str, api_key: str,
                   num_results: int = 50, date: str = "w", gl: str = "us") -> list[dict]:
    """
    Call Serper.dev Google Search API.
    Paginates automatically. Each page = 10 results = 1 API credit.
    gl restricts results to a country (Google's "gl" param, e.g. "us").
    """
    tbs = DATE_MAP.get(date, "qdr:w")
    results: list[dict] = []
    pages = (num_results + 9) // 10

    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    with console.status("[cyan]Querying Serper...[/cyan]"):
        for page in range(pages):
            payload = {
                "q":    query,
                "num":  10,
                "page": page + 1,
                "tbs":  tbs,
            }
            if gl:
                payload["gl"] = gl

            try:
                resp = requests.post(SERPER_URL, headers=headers,
                                      json=payload, timeout=15)
            except requests.RequestException as e:
                console.print(f"[red]Request error on page {page + 1}: {e}[/red]")
                break

            if resp.status_code == 401:
                raise RuntimeError("401 Unauthorized — check your SERPER_API_KEY.")

            if resp.status_code == 429:
                console.print("[yellow]429 — daily quota exceeded on Serper free tier.[/yellow]")
                break

            if resp.status_code != 200:
                raise RuntimeError(f"Serper API error {resp.status_code}: {resp.text[:300]}")

            data = resp.json()

            if page == 0:
                credits = data.get("credits")
                if credits is not None:
                    console.print(f"[dim]Serper credits remaining: {credits}[/dim]")

            if os.environ.get("SERPER_DEBUG", "").strip().lower() in ("1", "true", "yes"):
                print(f"\n--- Raw Serper response (page {page + 1}) ---")
                print(json.dumps(data, indent=2))
                print("--- end raw response ---\n")

            organic = data.get("organic", [])
            if not organic:
                break

            for item in organic:
                results.append({
                    "Title":   item.get("title", "").strip(),
                    "Domain":  item.get("link", "").split("/")[2] if item.get("link") else "",
                    "URL":     item.get("link", ""),
                    "Snippet": item.get("snippet", "").replace("\n", " ").strip(),
                    "Date":    item.get("date", ""),
                })

            if len(organic) < 10:
                break  # last page

    return results[:num_results]


def search_multiple_queries(queries: dict[str, str], api_key: str,
                             num_results: int = 50, date: str = "w", gl: str = "us",
                             on_progress=None) -> list[dict]:
    """
    Run search_serper once per (label, query) pair, tag each row with the
    label it came from, and return the combined, URL-deduplicated results.
    num_results, date, and gl are applied identically to every query.

    on_progress, if given, is called with a plain-text status line per query
    (in addition to the rich console output) — pass it through from a caller
    like Streamlit that can't see this process's stdout.
    """
    all_results: list[dict] = []
    seen_urls: set[str] = set()

    for label, query in queries.items():
        console.print(f"[cyan]Running query:[/cyan] {label}")
        if on_progress:
            on_progress(f"Running query: {label}")

        results = search_serper(query, api_key, num_results=num_results, date=date, gl=gl)
        added = 0
        for row in results:
            if row["URL"] in seen_urls:
                continue
            seen_urls.add(row["URL"])
            row["Query"] = label
            all_results.append(row)
            added += 1

        summary = f"  -> {len(results)} result(s) from Serper, {added} new after dedup"
        console.print(summary + "\n")
        if on_progress:
            on_progress(summary)

    return all_results


# ---------------------------------------------------------------------------
# Display (CLI only)
# ---------------------------------------------------------------------------

def display_table(results: list[dict]) -> None:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        show_lines=False,
        expand=True,
    )
    table.add_column("#",       style="dim",         width=4,  no_wrap=True)
    table.add_column("Title",   style="white",       min_width=28, max_width=46)
    table.add_column("Domain",  style="green",       min_width=18, max_width=30)
    table.add_column("Posted",  style="yellow",      width=12, no_wrap=True)
    table.add_column("URL",     style="bright_blue", min_width=36, overflow="fold")

    for i, r in enumerate(results, 1):
        table.add_row(str(i), r["Title"], r["Domain"], r["Date"], r["URL"])

    console.print(table)


# ---------------------------------------------------------------------------
# Google Drive upload (mirrors run_apify_to_drive.py's _build_drive_service)
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


def _build_csv_bytes(results: list[dict]) -> bytes:
    fieldnames = ["Title", "Domain", "Date", "URL", "Snippet"]
    if results and "Query" in results[0]:
        fieldnames.append("Query")
    text_buffer = io.StringIO()
    writer = csv.DictWriter(text_buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in results:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return text_buffer.getvalue().encode("utf-8")


def upload_csv_to_drive(results: list[dict], folder_id: str, name_prefix: str,
                         as_google_sheet: bool = False) -> tuple[str, str]:
    """
    Upload results to Drive. If as_google_sheet is True, the CSV is uploaded
    with a Google Sheets target mimeType, which Drive auto-converts into a
    native spreadsheet instead of a flat .csv file.
    """
    csv_bytes = _build_csv_bytes(results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if as_google_sheet:
        file_name = f"{name_prefix}_googleSearch_{timestamp}"
        target_mime = "application/vnd.google-apps.spreadsheet"
    else:
        file_name = f"{name_prefix}_googleSearch_{timestamp}.csv"
        target_mime = "text/csv"

    service = _build_drive_service()
    media = MediaIoBaseUpload(io.BytesIO(csv_bytes), mimetype="text/csv", resumable=False)
    result = (
        service.files()
        .create(
            body={"name": file_name, "parents": [folder_id], "mimeType": target_mime},
            media_body=media,
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )
    return result.get("id", ""), result.get("name", file_name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Google Job Search via Serper.dev → Google Sheet uploaded to Drive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Date options:
  h    past hour
  d    past 24 hours  (default)
  w    past week
  m    past month
  y    past year

Examples:
  python google_search_to_drive.py
  python google_search_to_drive.py --date w
  python google_search_to_drive.py --query "site:careers.withwaymo.com data scientist"
  python google_search_to_drive.py --num 100 --date m
        """,
    )
    parser.add_argument("--query", "-q", default=DEFAULT_QUERY,
                         help="Search query")
    parser.add_argument("--api-key", default=os.getenv("SERPER_API_KEY"),
                         help="Serper API key (or set SERPER_API_KEY env var)")
    parser.add_argument("--num", "-n", type=int, default=10,
                         help="Max results (default 10)")
    parser.add_argument("--date", "-d", default="d",
                         choices=["h", "d", "w", "m", "y"],
                         help="Time range: h=hour d=day w=week m=month y=year (default: d)")
    parser.add_argument("--gl", default="us",
                         help="Country to restrict results to, Google's gl code (default: us)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.api_key:
        console.print("[red]✗ Missing Serper API key.[/red]")
        console.print("  Sign up free at https://serper.dev")
        console.print("  Then set SERPER_API_KEY in .env")
        console.print("  Or:    python google_search_to_drive.py --api-key YOUR_KEY")
        sys.exit(1)

    folder_id = _require_env("GOOGLE_DRIVE_ANALYSIS_FOLDER_ID")

    date_label = {"h": "past hour", "d": "past 24h", "w": "past week",
                  "m": "past month", "y": "past year"}[args.date]

    console.rule("[bold cyan]Google Job Search[/bold cyan]")
    console.print(f"[dim]Query :[/dim] {args.query[:110]}{'...' if len(args.query) > 110 else ''}")
    console.print(f"[dim]Range :[/dim] {date_label}  |  [dim]Max:[/dim] {args.num} results\n")

    results = search_serper(
        query=args.query,
        api_key=args.api_key,
        num_results=args.num,
        date=args.date,
        gl=args.gl,
    )

    if not results:
        console.print("[yellow]No results. Try broadening the query or date range.[/yellow]")
        sys.exit(0)

    console.print(f"\n[bold]{len(results)} results[/bold]\n")
    display_table(results)

    source_label = re.sub(r"[^a-z0-9]+", "_", args.query.lower())[:40].strip("_") or "search"
    console.print(f"\nUploading Google Sheet to Drive folder {folder_id} …")
    file_id, file_name = upload_csv_to_drive(results, folder_id, source_label, as_google_sheet=True)
    console.print(f"[bold green]✓ Uploaded {len(results)} row(s) → {file_name}[/bold green] (file_id={file_id})")


if __name__ == "__main__":
    main()
