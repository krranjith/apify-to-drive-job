#README.md

# job-match — Python + Claude API port

A standalone Python implementation of the job-match pipeline. Same flow, scoring rubric,
thresholds, schema, resume template, and idempotency as the Cowork skill version — the only
difference is that judgment runs via the Claude **Messages API** instead of the Cowork agent,
and I/O paths are centralized in `config.py`.

## What maps to what

| Original skill | Python module | Nature |
|---|---|---|
| `fetch-job-links` | `fetch_links.py` | requests + JSON-LD (HTML-region fallback) |
| `evaluate-jobs` | `evaluate.py` + `prompts/evaluate_jobs.md` | 1 Claude call/job |
| `tailor-resume` | `tailor.py` + `prompts/tailor_resume.md` | 1 Claude call/Apply-Review row |
| `build-outputs` | `build_outputs.py` | openpyxl + `render_resume.py` |
| `run-pipeline` | `pipeline.py` | argparse orchestrator + Status write-back |
| (shared) | `jobs_io.py` | CSV read / mojibake / dedup / Status gate |
| (shared) | `config.py`, `llm.py`, `grounding.py` | paths, API wrapper, injected context |
| renderer | `render_resume.py` | **copied verbatim (md5-identical)** |
| canonical content | `canonical_resume.json` | **copied verbatim** |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate    # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                   # (Windows: setx ANTHROPIC_API_KEY ...)
```

Fonts: `render_resume.py` uses Carlito. On Linux it's at `/usr/share/fonts/truetype/crosextra`.
On Windows/macOS, set `RESUME_FONTDIR` to a folder containing `Carlito-Regular/Bold/Italic/
BoldItalic.ttf` (Carlito is OFL; bundle the TTFs if needed).

## Expected layout (paths from `config.py`, override with `JOBMATCH_ROOT`)

```
<root>/02-project/source-docs/jobs.csv            # required
<root>/02-project/source-docs/Links.csv           # links mode only (URL,STATUS)
<root>/02-project/source-docs/Ranjith_resume.pdf  # grounding (falls back to canonical json)
<root>/02-project/source-docs/Ranjith_Resume_DataScience_Extended.docx
<root>/02-project/DATA-CONTRACT.md
<root>/02-project/outputs/run_*/                   # created per run
<root>/04-archive/                                 # archive-before-write
```

`render_resume.py` and `canonical_resume.json` live next to the code in `python-app/`. If you
relocate the renderer, keep the JSON beside it (the script loads it relative to its own dir).

## Run

```bash
# calibration: score first 4 jobs, then STOP (no tailoring/build/write-back)
python pipeline.py --mode job --calibrate 4

# full job pipeline (jobs.csv direct)
python pipeline.py --mode job

# links pipeline (fetch Links.csv URLs first, then score)
python pipeline.py --mode links
```

Individual stages can be run alone (each is resumable from the run folder's `evaluations.jsonl`):

```bash
python evaluate.py --calibrate 4
python tailor.py          # operates on the latest run_* folder
python build_outputs.py   # operates on the latest run_* folder
python fetch_links.py
```

## Behavior guarantees (identical to the original)

- Scores **only** blank-`Status` rows; dedups jobs.csv + Link_jobs.csv on `applyUrl`.
- Two-stage filter; **Apply ≥ 75 / Review 55–74 / Skip < 55**; ATS 50% + recruiter 50%.
- Reframe-only, strict **1:1 nested-groups** tailoring — validated in `tailor.py._validate`,
  not just requested in the prompt.
- Excel written first (Apply/Review/Skip), then **Apply-only** one-page PDFs.
- Full run writes `Status=Processed` back keyed on `applyUrl`, archive-first. Calibration and
  early-abort runs never write back.
- `temperature=0` + pinned model (`claude-opus-4-8`) for reproducible scoring.

## Notes

- `MODEL` is pinned in `config.py`; verify against Anthropic's `/v1/models` before production.
- The Cowork `.claude/skills` version and this app can coexist — they read the same source
  files and write the same run-folder layout.
