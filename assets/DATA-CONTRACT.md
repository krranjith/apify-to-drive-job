---
type: reference
project: job-match
created: 2026-06-21
status: active
---

# Data Contract — shared schema for the job-match pipeline

The pipeline has five skills. `fetch-job-links` (optional front stage) produces a source
CSV from a list of URLs; `evaluate-jobs` → `tailor-resume` → `build-outputs` read and write
a single streaming intermediate file; `run-pipeline` orchestrates the chain and owns the
end-of-run Status write-back. Decoupling judgment (LLM) from rendering (Python/VM) lets each
stage run and re-run independently.

## Source inputs
The two CSVs below are **read-only for scoring/rendering**, with ONE exception: at the end of
a successful full run, `run-pipeline` writes `Status = Processed` back into them (see
**Status lifecycle**). The resume/grounding docs are always read-only.

- `02-project/source-docs/jobs.csv` — the curated job set (always present). **Columns:**
  `jobId, title, companyName, url, applyUrl, skills_required, jobDescription, Status`.
- `02-project/source-docs/Link_jobs.csv` — **optional**, produced by `fetch-job-links` from
  the URLs in `Links.xlsx`. **Identical column schema to `jobs.csv`**, so the two concatenate
  cleanly. Present only after a links run; absent otherwise.
  **Column mapping into the schema below (both files):** `title` → `jobTitle`; `applyUrl` →
  `jobLink` (use the apply link, NOT `url`); `companyName`, `jobId`, `jobDescription`,
  `Status` map by name. `url` (the JD posting link) is ignored. `skills_required` is a
  pre-extracted ATS keyword list — NOT used in scoring; carried through verbatim into a
  `skillsRequired` field and surfaced as an Excel column only. Blank `skills_required` →
  empty string (it is blank for a large share of `jobs.csv` rows).
- `02-project/source-docs/Ranjith_resume.pdf` — the one-page resume (canonical layout for tailored PDFs).
- `02-project/source-docs/Ranjith_Resume_DataScience_Extended.docx` — the grounding doc. **Every score and every tailored bullet must trace to a fact in here or the one-pager. Never invent.**

## Status lifecycle (the `Status` column — input-CSV only)
`Status` is the dedup/idempotency control for the source CSVs. Do not confuse it with the
record-level `status` verdict (lowercase: Apply/Review/Skip).
- **blank / NULL** → not yet processed. `evaluate-jobs` enqueues **only** these rows.
- **`Processed`** → written back by `run-pipeline` after a FULL run completes through
  `build-outputs`. These rows are skipped on every later run, even if the files are never
  deleted. (Calibration runs and early-aborted runs never write this.)
- **`FETCH_FAILED`** → written by `fetch-job-links` when a URL could not be fetched/parsed.
  Non-blank, so `evaluate-jobs` skips it too (no usable JD to score).
The single gate "process iff `Status` is blank" therefore covers both already-done jobs and
fetch failures.

### Encoding note
The source CSVs can contain mojibake (e.g. `ï¿½` where an em-dash/quote should be). When
reading either `jobs.csv` or `Link_jobs.csv`, decode tolerantly (try `utf-8`, fall back to
`cp1252`/`latin-1`) and normalize stray `ï¿½` / non-breaking junk to a plain `-` or space
before evaluating. Do not let encoding artifacts leak into scores, comments, or the resume.

### Cross-file de-duplication
When both CSVs feed a run, de-duplicate on `applyUrl`: a URL present in both files is scored
**once** (first occurrence wins). The duplicate's `jobId` will NOT appear in
`evaluations.jsonl`, so the Status write-back keys on `applyUrl` (not only `jobId`) to mark
**all** source rows for a scored URL as `Processed` — otherwise the unscored duplicate would
be re-picked next run. See `run-pipeline` Step 3b.

## Run folder (per-run, timestamped, never overwritten)
Every full run creates its own folder so prior runs are preserved (matches the
"never delete, archive instead" rule):

```
02-project/outputs/run_YYYY-MM-DD_HHMMSS/
├─ evaluations.jsonl        ← intermediate judgment, self-contained to this run
├─ Apply.xlsx               ← status == Apply, sorted by scorePost desc
├─ Review.xlsx              ← status == Review, sorted by scorePost desc
├─ Skip.xlsx                ← status == Skip, with dropReason / missingSkills
└─ Ranjith_<Company>.pdf   ← one-page tailored PDF per APPLY job only, LOOSE in the run folder
                              (Review/Skip get no PDF; on duplicate company, append _2, _3, …)
```

`evaluate-jobs` creates the run folder and is the owner of its path; `tailor-resume`
and `build-outputs` operate on the most recent run folder (or one passed explicitly).

## Intermediate file (the contract)
`<run-folder>/evaluations.jsonl` — **one JSON object per line, one line per job.**
Append-only streaming: process one job, write one line, move on (scales to ~50 without context overflow).

### Schema (one record)
```json
{
  "jobId": "1",
  "companyName": "string",
  "jobTitle": "string",
  "jobLink": "string — from CSV applyUrl",
  "sourceFile": "string — 'jobs.csv' or 'Link_jobs.csv'; which CSV this row came from (used by the Status write-back)",
  "Status": "string — verbatim input Status at read time (blank when freshly scored); distinct from the lowercase 'status' verdict below",
  "skillsRequired": "string — verbatim passthrough of CSV skills_required (NOT used in scoring); Excel column only",
  "stage1": { "passed": true, "dropReason": null },
  "scorePre": 0,
  "scorePost": 0,
  "status": "Apply | Review | Skip",
  "atsScore": 0,
  "recruiterScore": 0,
  "comment": "string — rationale + any high-impact change that belongs OUTSIDE the resume",
  "outreach": "string — <=300 chars, recruiter, warm-direct",
  "keywordsMatched": ["..."],
  "keywordsMissing": ["... — ATS literal: JD terms absent from the resume"],
  "missingSkills": ["... — recruiter judgment: genuine capability gaps that hurt fit"],
  "tailored": null,
  "evaluatedAt": "2026-06-21"
}
```

### `tailored` block (added by `tailor-resume`, only for Apply/Review)
**Paired schema — every entry carries the original bullet verbatim AND its suggested replacement.**
This pairing is what powers the side-by-side "Current Bullets" / "Suggested Replacement Bullets"
columns in `Apply.xlsx`/`Review.xlsx`, and it lets `build-outputs` do a pure find-and-replace on the
canonical resume (swap `original` → `replacement`, change nothing else).

```json
"tailored": {
  "experience": [
    {
      "role": "Senior Business Planning Specialist (Data Scientist) | AMD Inc",
      "bullets": [
        { "original": "<verbatim bullet from the canonical resume>",
          "replacement": "<tailored bullet, or identical to original if no improvement applies>",
          "changed": true }
      ]
    }
  ],
  "projects": [
    { "name": "Customer Segmentation & Revenue Map",
      "original": "<verbatim project line from the canonical resume>",
      "replacement": "<tailored line, or identical to original>",
      "changed": false }
  ],
  "onePageOk": true,
  "notes": "what was reframed and why; confirmation nothing was invented"
}
```

**Pairing rules (1:1, keep-as-is allowed):**
- One object per canonical bullet — same count, same order, same roles/projects as the source
  resume. No bullets added, dropped, reordered, or merged.
- `original` MUST be the exact source-resume text (so `build-outputs` can string-match it).
- `replacement` is the tailored bullet. If a bullet is already strong and the JD warrants no
  change, set `replacement` == `original` and `changed: false`.
- Scope is **Experience + Projects bullets only**. Header, Summary, Skills, Education,
  titles, dates, and company names are out of scope and never appear here.

## Field rules
- `scorePre` = score before tailoring. `scorePost` = expected score after tailoring (== scorePre for Skip; only Apply/Review get a meaningful lift).
- `status` derives from `scorePost`: **Apply ≥ 75, Review 55–74, Skip < 55** (calibration default; revisit after first run).
- `atsScore` + `recruiterScore` each 0–100; overall = mean (50/50). Record both for transparency.
- `keywordsMissing` vs `missingSkills` are distinct: the former is mechanical (a JD term not
  literally in the resume), the latter is recruiter judgment (a real capability gap that
  affects fit — e.g. "no production MLOps", "classical ML only, no deep learning at scale").
  Both become columns in the Excel output.
- `stage1.passed == false` → status is `Skip`, `dropReason` set, scoring fields may be 0, no `tailored` block.
- `outreach` ≤ 300 characters, addressed to a recruiter, warm-direct tone, leads with fit + one quantified hook.
- `tailored` stays `null` for Skip jobs.

## Final outputs (written by `build-outputs`, VM required) — all inside the run folder
**Order is fixed: write ALL THREE Excel files first, then generate PDFs.** The Excel is the review
artifact and the PDFs are derived from the same `original`/`replacement` pairs the Excel shows.
- `<run-folder>/Apply.xlsx` — rows where status == Apply, sorted by `scorePost` desc.
- `<run-folder>/Review.xlsx` — rows where status == Review, sorted by `scorePost` desc.
  Apply.xlsx and Review.xlsx share the SAME columns: scorePre, scorePost, status, atsScore,
  recruiterScore, keywordsMatched, keywordsMissing, **missingSkills**, **skillsRequired**
  (verbatim CSV passthrough), comment, outreach, jobLink, **Current Bullets**,
  **Suggested Replacement Bullets**. The last two are built from `tailored` — paste each bullet
  on its own line within the cell, in the same order, so a reader can compare row-aligned
  original vs replacement. (Review rows are still tailored; they just get no PDF.)
- `<run-folder>/Skip.xlsx` — rows where status == Skip, with `dropReason`, `missingSkills`, `skillsRequired`, `comment`.
- `<run-folder>/Ranjith_<Company>.pdf` — one-page tailored PDF **per APPLY job only** (Review and
  Skip get no PDF), loose in the run folder. **Rendered by `02-project/assets/scripts/render_resume.py`** (do not
  hand-build the layout). It performs an **in-place bullet swap**: starts from the canonical
  resume and replaces each `original` bullet with its `replacement`; header, Summary, Skills,
  Education, section structure, and bullet counts are unchanged. Sanitize company name
  (alnum + underscores). If the same company appears more than once in a run, append a
  numeric suffix to keep names unique: `Ranjith_Stripe.pdf`, `Ranjith_Stripe_2.pdf`, …
  **Template fidelity (matches `source-docs/Ranjith_resume_template_anthropic.pdf`):** US Letter
  (612×792); Carlito font (installed on the VM at `/usr/share/fonts/truetype/crosextra/`, override
  via `RESUME_FONTDIR`); Carlito-Bold 16.5pt centered name; centered contact line with blue
  (#1155CC) links, grey `|` separators, and a thin black rule beneath; full-width light-grey
  (#EDEDED) section bars with thin black top/bottom rules and centered dark-blue (#1F3A8A)
  labels; right-aligned italic role + education dates; two-column Education (degree left, GPA+date
  right); `boldify()` auto-bolds quantified metrics (%, $, 4x, K/M/B, SKUs) in Experience +
  Project bullets; auto-fit loop shrinks spacing to fill exactly one full page. The base resume's
  static text (header/Summary/Skills/Education) lives as constants in the script — update them
  only if the underlying one-pager changes. (Prior template was A4 + Inter; Inter TTFs remain in
  `02-project/assets/fonts/` if a switch-back is ever needed.)

## Re-run semantics
- `fetch-job-links` (optional, links mode) reads `Links.xlsx` (`URL` + `Status` columns),
  fetches **only** rows whose `Status` is blank/NULL (skipping `Processed` rows), writes
  `Link_jobs.csv` into `source-docs/` (archiving any prior copy), marks unfetchable URLs
  `FETCH_FAILED` in the CSV, and flips the successfully-fetched URLs to `Status=Processed`
  in `Links.xlsx` (archiving the workbook first). `FETCH_FAILED` URLs are left blank in
  `Links.xlsx` so they retry next run.
- Each `evaluate-jobs` run creates a fresh `run_YYYY-MM-DD_HHMMSS/` folder and writes its own
  `evaluations.jsonl`. It scores **only** source rows whose `Status` is blank (see Status
  lifecycle), reading both CSVs when present.
- `tailor-resume` reads the latest run's file, fills `tailored` for Apply/Review rows that lack it, rewrites in place.
- `build-outputs` is pure rendering — never mutates judgment fields, only reads the run's `evaluations.jsonl`.
- `run-pipeline` orchestrates the chain and, **only after a successful full run**, writes
  `Status = Processed` back into the source CSVs (keyed on `applyUrl`, archiving each file
  first). Calibration runs and runs that stop before `build-outputs` never write back.
- Idempotency: because processed rows are marked, re-running the pipeline scores only new
  (blank-Status) rows. To force a full re-score, clear the `Status` column in the source CSV.
