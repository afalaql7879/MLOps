"""PreToolUse hook: block training scripts whose datasets have no data-privacy approval.

Runs on every Bash / PowerShell tool call. It only acts when the command runs a Python script
that looks like model training (fit/train/Trainer/autolog...). For each local dataset file the
script or command references, it requires an APPROVED or CONDITIONAL entry with the same SHA-256
in .claude/privacy/approvals.jsonl (written by the data-privacy skill's record_approval.py).

- Unapproved / BLOCKED / changed dataset  -> deny, and tell Claude to run the data-privacy skill.
- Remote or database source it can't hash -> ask the user.
- Not a training script, or all approved  -> allow silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

PY_SCRIPT_RE = re.compile(
    r"""(?:^|[\s;&|(])(?:python3?|py)(?:\.exe)?"?((?:\s+-[A-Za-z]\w*)*)\s+("[^"]+\.py"|'[^']+\.py'|[^\s;&|"']+\.py)""",
    re.IGNORECASE,
)
TRAINING_RE = re.compile(
    r"\.fit\(|\.partial_fit\(|\.fit_transform\(.*\n.*\.fit\(|\bTrainer\(|\bSFTTrainer\(|\.train\(|"
    r"\b(?:xgb|lgb|lightgbm|xgboost|catboost)\.train\(|\bautolog\(|train_test_split|fine_?tun|"
    r"optimizer\.step\(|loss\.backward\(",
    re.IGNORECASE,
)
DATA_EXT = r"csv|tsv|parquet|jsonl|ndjson|json|xlsx|xls|feather|arrow|pkl|pickle|npz|npy|h5|hdf5|sav|dta|sqlite|db|txt"
DATA_LITERAL_RE = re.compile(r"""["']([^"'\n]+\.(?:%s))["']""" % DATA_EXT, re.IGNORECASE)
DATA_ARG_RE = re.compile(r"""(?:^|\s)["']?([^\s"';&|]+\.(?:%s))["']?""" % DATA_EXT, re.IGNORECASE)
REMOTE_RE = re.compile(
    r"read_sql|create_engine\(|psycopg|pyodbc|cx_Oracle|oracledb|snowflake|bigquery|"
    r"[\"'](?:s3|gs|abfss?|hdfs|https?)://|load_dataset\(",
    re.IGNORECASE,
)
# files that are never training data
IGNORED_NAMES = {"mlflow.db", "requirements.txt", "privacy_report.json", "approvals.jsonl"}


def project_dir(payload: dict) -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".").resolve()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_ledger(proj: Path) -> dict[str, dict]:
    ledger = proj / ".claude" / "privacy" / "approvals.jsonl"
    entries: dict[str, dict] = {}
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                entries[e["sha256"]] = e  # latest decision for a hash wins
    return entries


def resolve(ref: str, bases: list[Path]) -> Path | None:
    p = Path(ref)
    for cand in ([p] if p.is_absolute() else [b / p for b in bases]):
        if cand.is_file():
            return cand.resolve()
    return None


def decide(decision: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))


def main() -> int:
    payload = json.load(sys.stdin)
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command or "python" not in command.lower() and "py " not in command.lower():
        return 0

    proj = project_dir(payload)
    cwd = Path(payload.get("cwd") or proj)
    scripts = []
    for m in PY_SCRIPT_RE.finditer(command):
        ref = m.group(2).strip("\"'")
        path = resolve(ref, [cwd, proj])
        if path and ".claude" not in path.parts:  # the steward's own tools are never gated
            scripts.append(path)
    if not scripts:
        return 0

    ledger = None
    problems, unverifiable = [], []
    for script in scripts:
        src = script.read_text(encoding="utf-8", errors="replace")
        if not TRAINING_RE.search(src):
            continue
        refs = set(DATA_LITERAL_RE.findall(src)) | set(DATA_ARG_RE.findall(command))
        if REMOTE_RE.search(src):
            unverifiable.append(f"{script.name} reads from a database/remote source that can't be hashed")
        for ref in sorted(refs):
            if Path(ref).name in IGNORED_NAMES:
                continue
            path = resolve(ref, [cwd, script.parent, proj])
            if path is None:
                if not re.search(r"(?:out|output|pred|result|report|metrics)", ref, re.IGNORECASE):
                    unverifiable.append(f"{script.name} references '{ref}', which was not found to check")
                continue
            if ledger is None:
                ledger = load_ledger(proj)
            entry = ledger.get(sha256(path))
            rel = path.relative_to(proj).as_posix() if path.is_relative_to(proj) else str(path)
            if entry is None:
                problems.append(f"'{rel}' has no data-privacy approval for this file version")
            elif entry.get("verdict") not in ("APPROVED", "CONDITIONAL"):
                problems.append(f"'{rel}' was {entry.get('verdict')} by the data steward on {entry.get('date')}")

    if problems:
        decide("deny",
               "Privacy gate (KSA PDPL/NDMO): " + "; ".join(problems) + ". "
               "Load the data-privacy skill, run its gate on each dataset (pii_scan.py, classification, "
               "legal basis, minimisation), then record the decision with "
               ".claude/skills/data-privacy/scripts/record_approval.py before training. "
               "Do not record an approval the user has not confirmed.")
    elif unverifiable:
        decide("ask",
               "Privacy gate: " + "; ".join(unverifiable) + ". Confirm this data source has a "
               "data-privacy (PDPL/NDMO) approval before training.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - never break unrelated shell commands
        print(f"privacy_gate: internal error, not gating this command: {exc}", file=sys.stderr)
        sys.exit(0)
