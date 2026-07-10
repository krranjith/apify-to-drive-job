#evalaute.py

"""
evaluate-jobs (Python + Claude API). Creates a timestamped run folder, streams one job at a
time to Claude for the two-stage judgment, and appends one JSON line per job to
evaluations.jsonl. Scores ONLY blank-Status rows; dedups across jobs.csv + Link_jobs.csv.
Judgment logic is unchanged — it lives in prompts/evaluate_jobs.md + the injected grounding.
"""
from __future__ import annotations
import datetime as dt
import json
from pathlib import Path

import config
import grounding
import jobs_io
import llm

_SYSTEM = None


def _system_prompt() -> str:
    global _SYSTEM
    if _SYSTEM is None:
        base = (config.PROMPTS / "evaluate_jobs.md").read_text(encoding="utf-8")
        _SYSTEM = base + "\n\n" + grounding.grounding_block()
    return _SYSTEM


def _user_payload(job: dict) -> str:
    return (
        "Score this job. Return exactly one JSON object per the output schema.\n\n"
        f"jobTitle: {job['jobTitle']}\n"
        f"companyName: {job['companyName']}\n"
        f"jobDescription:\n{job['jobDescription']}\n"
    )


def _finalize(job: dict, verdict: dict) -> dict:
    """Overlay the model's judgment onto the caller-owned identity/passthrough fields."""
    verdict = dict(verdict)
    verdict.update({
        "jobId": job["jobId"],
        "companyName": job["companyName"],
        "jobTitle": job["jobTitle"],
        "jobLink": job["jobLink"],
        "applyUrl": job["applyUrl"],
        "sourceFile": job["sourceFile"],
        "Status": job["Status"],                 # verbatim input Status (blank when fresh)
        "skillsRequired": job["skillsRequired"], # passthrough
        "evaluatedAt": dt.date.today().isoformat(),
        # Transient: carried so the stateless tailor stage has the JD to mirror.
        # tailor.py strips this before the final write so the on-disk record stays
        # faithful to the DATA-CONTRACT schema.
        "jobDescription": job["jobDescription"],
    })
    verdict.setdefault("tailored", None)
    return verdict


def new_run_folder() -> Path:
    ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    folder = config.OUTPUTS / f"run_{ts}"
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "evaluations.jsonl").touch()
    return folder


def run(calibrate: int | None = None, queue: list[dict] | None = None) -> Path:
    if queue is None:
        queue, stats = jobs_io.build_work_queue()
        print(f"[evaluate] rows total={stats['total']} already_processed={stats['already_processed']} "
              f"fetch_failed={stats['fetch_failed']} duplicates={stats['duplicates']} "
              f"to_score={len(queue)}")
    else:
        print(f"[evaluate] to_score={len(queue)} (queue provided by caller)")
    if calibrate:
        queue = queue[:calibrate]
        print(f"[evaluate] CALIBRATION: scoring first {len(queue)} only.")

    folder = new_run_folder()
    jsonl = folder / "evaluations.jsonl"
    counts = {"Apply": 0, "Review": 0, "Skip": 0}
    top: list[tuple[int, str, str]] = []

    with jsonl.open("a", encoding="utf-8") as fh:
        for i, job in enumerate(queue, 1):
            verdict = llm.complete_json(_system_prompt(), _user_payload(job))
            rec = _finalize(job, verdict)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            counts[rec.get("status", "Skip")] = counts.get(rec.get("status", "Skip"), 0) + 1
            top.append((rec.get("scorePost", 0), rec["companyName"], rec.get("status", "")))
            print(f"  [{i}/{len(queue)}] {rec['companyName'][:32]:32} "
                  f"{rec.get('status',''):6} scorePost={rec.get('scorePost',0)}")

    print(f"[evaluate] done -> {folder}")
    print(f"[evaluate] Apply={counts['Apply']} Review={counts['Review']} Skip={counts['Skip']}")
    for sc, co, st in sorted(top, reverse=True)[:5]:
        print(f"    top: {sc:3} {st:6} {co}")
    return folder


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", type=int, default=None, help="score only first N jobs")
    run(ap.parse_args().calibrate)
