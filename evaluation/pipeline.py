#pipeline.py

"""
run-pipeline (Python). Orchestrates the chain and owns the end-of-run Status write-back.
Two entry modes replace the agent's natural-language router:
    python pipeline.py --mode job                 # score jobs.csv directly
    python pipeline.py --mode links               # fetch-job-links first, then score
    python pipeline.py --mode job --calibrate 4   # score first 4 only, STOP (no write-back)
After a FULL run through build-outputs, sets Status=Processed in the source CSVs
(keyed on applyUrl, archive-first). Calibration / early-abort runs never write back.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import json
import shutil
from pathlib import Path

import config
import evaluate
import tailor
import build_outputs
import fetch_links


def _archive(path: Path):
    if path.exists():
        config.ARCHIVE.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        shutil.copy2(str(path), str(config.ARCHIVE / f"{path.stem}_{ts}{path.suffix}"))


def _writeback_processed(run_folder: Path) -> None:
    """Set Status=Processed on source rows whose applyUrl was scored this run (key=applyUrl)."""
    records = [json.loads(l) for l in (run_folder / "evaluations.jsonl")
               .read_text(encoding="utf-8").splitlines() if l.strip()]
    scored_urls = {r.get("applyUrl", "") for r in records if r.get("applyUrl")}

    for path in (config.JOBS_CSV, config.LINK_JOBS_CSV):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        reader = list(csv.reader(text.splitlines()))
        if not reader:
            continue
        header = reader[0]
        try:
            apply_i = header.index("applyUrl")
            status_i = header.index("Status")
        except ValueError:
            print(f"[writeback] {path.name}: no applyUrl/Status column, skipped.")
            continue
        _archive(path)
        flipped = 0
        out = [header]
        for row in reader[1:]:
            while len(row) <= max(apply_i, status_i):
                row.append("")
            if row[apply_i].strip() in scored_urls and row[status_i].strip() == "":
                row[status_i] = "Processed"
                flipped += 1
            out.append(row)
        with path.open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerows(out)
        print(f"[writeback] {path.name}: flipped {flipped} rows -> Processed")


def main() -> None:
    ap = argparse.ArgumentParser(description="job-match pipeline")
    ap.add_argument("--mode", choices=["job", "links"], default="job",
                    help="job = score jobs.csv directly; links = fetch-job-links first")
    ap.add_argument("--calibrate", type=int, default=None,
                    help="score only first N jobs then STOP (no tailoring/build/write-back)")
    args = ap.parse_args()

    if args.mode == "links":
        print("=== STAGE 0: fetch-job-links ===")
        fetch_links.run()

    print("=== STAGE 1: evaluate ===")
    run_folder = evaluate.run(calibrate=args.calibrate)

    if args.calibrate:
        print(f"\n[calibration] stopped after {args.calibrate} jobs. "
              f"Review {run_folder}/evaluations.jsonl, then run a full pass.")
        return

    print("=== STAGE 2: tailor ===")
    tailor.run(run_folder)

    print("=== STAGE 3: build-outputs ===")
    build_outputs.run(run_folder)

    print("=== STAGE 3b: write-back (full run only) ===")
    _writeback_processed(run_folder)

    print(f"\n=== DONE === {run_folder}")
    print(f"Deliverables: {run_folder}/Apply.xlsx, Review.xlsx, Skip.xlsx + Apply PDFs")


if __name__ == "__main__":
    main()
