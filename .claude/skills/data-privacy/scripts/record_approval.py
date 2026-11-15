"""Record a data-steward decision for one dataset version in the approvals ledger.

The privacy-gate hook (.claude/hooks/privacy_gate.py) lets a training script run only if every
local dataset it references has an APPROVED or CONDITIONAL entry here with the same SHA-256.

Usage:
    python record_approval.py <dataset> --report privacy_report.json --verdict APPROVED \
        --classification Restricted --legal-basis "statutory task of <entity>" \
        [--conditions "dropped national_id,phone"] [--dpo-ref <ref>] [--steward <name>]

The report must come from pii_scan.py on this exact file (hashes must match). If the scan found
sensitive (PDPL) data, a --dpo-ref is required. The ledger holds hashes and metadata only, no data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[3] / "privacy" / "approvals.jsonl"  # .claude/privacy/
CLASSIFICATIONS = ["Public", "Restricted", "Secret", "Top Secret"]
VERDICTS = ["APPROVED", "CONDITIONAL", "BLOCKED"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset")
    ap.add_argument("--report", required=True, help="privacy_report.json written by pii_scan.py")
    ap.add_argument("--verdict", required=True, choices=VERDICTS)
    ap.add_argument("--classification", required=True, choices=CLASSIFICATIONS)
    ap.add_argument("--legal-basis", required=True)
    ap.add_argument("--conditions", default="")
    ap.add_argument("--dpo-ref", default="")
    ap.add_argument("--steward", default="")
    a = ap.parse_args()

    ds = Path(a.dataset)
    if not ds.is_file():
        print(f"error: {ds} not found", file=sys.stderr)
        return 2
    digest = sha256(ds)
    report = json.loads(Path(a.report).read_text(encoding="utf-8"))
    if report.get("sha256") != digest:
        print("error: report was produced for a different file version; re-run pii_scan.py", file=sys.stderr)
        return 2

    verdict = a.verdict
    if a.classification in ("Secret", "Top Secret") and verdict != "BLOCKED":
        print("error: Secret/Top Secret data cannot be approved for training here", file=sys.stderr)
        return 2
    if report.get("sensitive_columns") and verdict != "BLOCKED" and not a.dpo_ref:
        print("error: scan found PDPL sensitive data; --dpo-ref (DPO approval reference) is required",
              file=sys.stderr)
        return 2
    if verdict == "CONDITIONAL" and not a.conditions:
        print("error: CONDITIONAL needs --conditions describing what was applied", file=sys.stderr)
        return 2
    if not a.legal_basis.strip():
        print("error: --legal-basis must not be empty", file=sys.stderr)
        return 2

    entry = {
        "sha256": digest,
        "dataset": ds.as_posix(),
        "verdict": verdict,
        "classification": a.classification,
        "legal_basis": a.legal_basis,
        "conditions": a.conditions,
        "dpo_ref": a.dpo_ref,
        "steward": a.steward,
        "direct_identifiers": report.get("direct_identifiers", []),
        "sensitive_columns": report.get("sensitive_columns", []),
        "date": date.today().isoformat(),
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"recorded {verdict} for {ds} ({digest[:12]}...) in {LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
