#tailor.py


"""
tailor-resume (Python + Claude API). For each Apply/Review row lacking a `tailored` block,
calls Claude once to produce the nested-groups paired-bullet block, validates it against the
canonical resume structure (strict 1:1), and rewrites the jsonl line in place. All other
fields are preserved untouched. Reframe-only logic lives in prompts/tailor_resume.md.
"""
from __future__ import annotations
import json
from pathlib import Path

import config
import grounding
import llm

_SYSTEM = None


def _system_prompt() -> str:
    global _SYSTEM
    if _SYSTEM is None:
        base = (config.PROMPTS / "tailor_resume.md").read_text(encoding="utf-8")
        _SYSTEM = base + "\n\n" + grounding.grounding_block()
    return _SYSTEM


def latest_run() -> Path:
    runs = sorted(config.OUTPUTS.glob("run_*"), key=lambda p: p.name)
    if not runs:
        raise FileNotFoundError("No run_* folder found in outputs/.")
    return runs[-1]


def _canon_shape() -> dict:
    """Expected structure: role -> [ (label, bullet_count), ... ]; plus project count."""
    canon = grounding.canonical_resume()
    shape = {}
    for e in canon["experience"]:
        shape[e["role"]] = [(g.get("label", ""), len(g["bullets"])) for g in e["groups"]]
    return {"experience": shape, "projects": len(canon["projects"])}


def _validate(tailored: dict) -> list[str]:
    """Return a list of structural problems (empty = valid). Enforces strict 1:1 nesting."""
    problems = []
    shape = _canon_shape()
    exp = tailored.get("experience", [])
    got_roles = {e.get("role", ""): e for e in exp}
    for role, groups in shape["experience"].items():
        if role not in got_roles:
            problems.append(f"missing role: {role}")
            continue
        got_groups = got_roles[role].get("groups", [])
        if len(got_groups) != len(groups):
            problems.append(f"role '{role}': group count {len(got_groups)} != {len(groups)}")
            continue
        for idx, (label, n) in enumerate(groups):
            g = got_groups[idx]
            if g.get("label", "") != label:
                problems.append(f"role '{role}' group {idx}: label mismatch")
            if len(g.get("bullets", [])) != n:
                problems.append(f"role '{role}' group {idx}: bullet count {len(g.get('bullets', []))} != {n}")
    if len(tailored.get("projects", [])) != shape["projects"]:
        problems.append(f"projects count {len(tailored.get('projects', []))} != {shape['projects']}")
    return problems


def _user_payload(rec: dict) -> str:
    return (
        "Tailor the resume for this job. Return exactly one JSON object (the `tailored` block) "
        "with the nested-groups schema matching the canonical resume.\n\n"
        f"jobTitle: {rec.get('jobTitle','')}\n"
        f"companyName: {rec.get('companyName','')}\n"
        f"keywordsMissing: {rec.get('keywordsMissing', [])}\n"
        f"missingSkills: {rec.get('missingSkills', [])}\n"
        f"jobDescription:\n{rec.get('jobDescription','')}\n"
    )


def run(run_folder: Path | None = None) -> Path:
    folder = run_folder or latest_run()
    jsonl = folder / "evaluations.jsonl"
    records = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]

    tailored_count = 0
    warnings = []
    for rec in records:
        if rec.get("status") in ("Apply", "Review") and not rec.get("tailored"):
            block = llm.complete_json(_system_prompt(), _user_payload(rec))
            problems = _validate(block)
            if problems:
                warnings.append((rec.get("companyName", "?"), problems))
                print(f"  [WARN] {rec.get('companyName','?')}: {problems}")
            rec["tailored"] = block
            tailored_count += 1
            print(f"  tailored {rec.get('companyName','?')[:40]} "
                  f"(onePageOk={block.get('onePageOk')})")

    # Note: jobDescription is not part of the DATA-CONTRACT record; if present from evaluate
    # it is dropped here to keep the contract clean. Keep everything else.
    with jsonl.open("w", encoding="utf-8") as fh:
        for rec in records:
            rec.pop("jobDescription", None)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[tailor] tailored {tailored_count} Apply/Review rows in {folder}")
    if warnings:
        print(f"[tailor] {len(warnings)} rows had structural warnings (review above).")
    return folder


if __name__ == "__main__":
    run()
