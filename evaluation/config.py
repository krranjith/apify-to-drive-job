#config.py

"""
Central configuration — the ONLY place paths change between the Cowork agent and this
Python app. Logic, schema, thresholds, and template are identical to the original pipeline.

Source data (Jobs xlsx / googlesearch sheet) and final outputs now live in Google Drive
(see drive_io.py / drive_pipeline.py) — the paths below are local SCRATCH space used while
a run is in progress, plus the static grounding/prompt assets that ship in this repo.
"""
from __future__ import annotations
import os
from pathlib import Path

from dotenv import load_dotenv

# --- Root ---------------------------------------------------------------------
# All I/O is anchored here. Override with JOBMATCH_ROOT to relocate the project;
# nothing else about the flow changes.
APP_DIR = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("JOBMATCH_ROOT", APP_DIR.parent))

load_dotenv(ROOT / ".env")

# --- Source inputs (local CLI/legacy fallback; Drive mode bypasses these) -----
SOURCE_DOCS = ROOT / "02-project" / "source-docs"
JOBS_CSV = SOURCE_DOCS / "jobs.csv"
LINK_JOBS_CSV = SOURCE_DOCS / "Link_jobs.csv"
LINKS_CSV = SOURCE_DOCS / "Links.csv"

# --- Grounding docs (synced from Drive by drive_pipeline.sync_grounding_docs) -
CACHE_DIR = APP_DIR / ".cache"
RESUME_PDF = CACHE_DIR / "Ranjith_resume.pdf"
EXTENDED_DOCX = CACHE_DIR / "Ranjith_Resume_DataScience_Extended.docx"

# --- Assets (renderer + canonical content, copied verbatim) -------------------
ASSETS = ROOT / "assets"
RENDER_SCRIPT = APP_DIR / "render_resume.py"
CANONICAL_JSON = ASSETS / "canonical_resume.json"

# --- Contract + prompts -------------------------------------------------------
PROMPTS = APP_DIR / "PROMPTS"
DATA_CONTRACT = ASSETS / "DATA-CONTRACT.md"

# --- Outputs / archive (local scratch; final deliverables are uploaded to Drive)
OUTPUTS = APP_DIR / "runs"
ARCHIVE = APP_DIR / "archive"

# --- Google Drive folder IDs ---------------------------------------------------
ANALYSIS_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_ANALYSIS_FOLDER_ID", "").strip()
GROUNDING_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_GROUNDING_FOLDER_ID", "").strip()
EVALUATED_RESULTS_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_EVALUATED_RESULTS_FOLDER_ID", "").strip()

# --- Claude API ---------------------------------------------------------------
# Pinned snapshot (dateless IDs are still pinned per Anthropic docs). Verify against
# /v1/models before production. Sampling params (temperature/top_p/top_k) are
# removed on claude-opus-4-8 (400 if sent) — do not reintroduce them.
MODEL = os.environ.get("JOBMATCH_MODEL", "claude-opus-4-8")
MAX_TOKENS = 8000
API_KEY_ENV = "ANTHROPIC_API_KEY"

# --- Locked pipeline constants (do NOT change — parity with original) ---------
APPLY_THRESHOLD = 75          # scorePost >= 75  -> Apply
REVIEW_THRESHOLD = 55         # 55..74           -> Review; < 55 -> Skip
OUTREACH_MAX_CHARS = 300
CSV_COLUMNS = [               # exact source/Link_jobs schema, in order
    "jobId", "title", "companyName", "url", "applyUrl",
    "skills_required", "jobDescription", "Status",
]

# --- Renderer font dir (Carlito on the VM; override if needed) -----------------
RESUME_FONTDIR = os.environ.get("RESUME_FONTDIR", "/usr/share/fonts/truetype/crosextra")
