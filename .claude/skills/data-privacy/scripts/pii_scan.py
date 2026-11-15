"""Scan a tabular dataset for personal data under KSA rules (PDPL / NDMO).

Usage:
    python pii_scan.py <dataset> [--out privacy_report.json] [--sample-rows 50000] [--k 5]

Prints a summary and writes a JSON report. Only masked sample values ever leave this script.
Exit code: 0 = no personal data found, 1 = personal or sensitive data found, 2 = error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- value patterns
SAUDI_ID_RE = re.compile(r"(?<!\d)([12]\d{9})(?!\d)")
MOBILE_RE = re.compile(r"(?<!\d)(?:\+?966|00966|0)?5\d{8}(?!\d)")
IBAN_RE = re.compile(r"\bSA\d{2}[ ]?(?:\d{4}[ ]?){5}\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
HIJRI_RE = re.compile(r"\b14\d{2}[/-](?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|30)\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ARABIC_RE = re.compile(r"[؀-ۿ]")

# ---------------------------------------------------------------- column-name hints (EN + AR)
DIRECT_ID_HINTS = {
    "national_id": ["national_id", "nationalid", "nid", "id_number", "civil_id", "هوية", "رقم_الهوية", "السجل_المدني"],
    "iqama": ["iqama", "iqamah", "residence_id", "border_number", "اقامة", "إقامة", "رقم_الحدود"],
    "name": ["name", "full_name", "first_name", "last_name", "family_name", "father_name", "الاسم", "اسم", "العائلة"],
    "phone": ["phone", "mobile", "msisdn", "tel", "contact_no", "جوال", "هاتف"],
    "email": ["email", "e_mail", "mail", "بريد"],
    "address": ["address", "street", "building_no", "national_address", "short_address", "po_box", "عنوان", "العنوان"],
    "iban": ["iban", "account_no", "bank_account", "card_number", "حساب", "آيبان"],
    "passport": ["passport", "جواز"],
    "license": ["licen", "plate", "رخصة", "لوحة"],
    "geo": ["latitude", "longitude", "lat", "lon", "lng", "gps", "coordinates"],
    "ip": ["ip_address", "ipaddr", "client_ip"],
    "photo_biometric": ["photo", "image", "face", "fingerprint", "iris", "biometric", "بصمة", "صورة"],
}
QUASI_ID_HINTS = {
    "dob": ["dob", "birth", "date_of_birth", "تاريخ_الميلاد", "الميلاد"],
    "age": ["age", "العمر"],
    "gender": ["gender", "sex", "الجنس"],
    "nationality": ["nationality", "citizenship", "الجنسية"],
    "location": ["city", "district", "region", "province", "neighborhood", "zip", "postal", "المدينة", "الحي", "المنطقة"],
    "occupation": ["job", "occupation", "employer", "title", "المهنة", "الوظيفة", "جهة_العمل"],
    "marital": ["marital", "الحالة_الاجتماعية"],
}
# PDPL sensitive categories
SENSITIVE_HINTS = {
    "health": ["diagnos", "disease", "medical", "health", "icd", "medication", "patient", "blood", "disab", "pregnan",
               "تشخيص", "مرض", "صحي", "طبي", "دواء", "إعاقة"],
    "genetic": ["genetic", "gene", "dna", "genome", "وراثي", "جيني"],
    "biometric": ["fingerprint", "iris", "face_embedding", "biometric", "voiceprint", "بصمة"],
    "religion_belief": ["religion", "sect", "belief", "political", "party", "الديانة", "المذهب", "سياسي"],
    "ethnic_origin": ["ethnic", "race", "tribe", "tribal", "origin", "القبيلة", "العرق", "الأصل"],
    "criminal_security": ["criminal", "crime", "conviction", "arrest", "offence", "offense", "violation", "security_record",
                          "جنائي", "سوابق", "قضية", "مخالفة"],
    "unknown_parentage": ["unknown_parent", "foundling", "مجهول", "مجهولي_الأبوين"],
}


def norm(col: str) -> str:
    return re.sub(r"[\s\-\.]+", "_", str(col).strip().lower())


def match_hints(col: str, hints: dict[str, list[str]]) -> list[str]:
    c = norm(col)
    out = []
    for cat, keys in hints.items():
        for k in keys:
            # short ascii keys must match a whole token to avoid "lat" in "translation"
            if k.isascii() and len(k) <= 4:
                if k in c.split("_"):
                    out.append(cat)
                    break
            elif k in c:
                out.append(cat)
                break
    return out


def luhn_ok(s: str) -> bool:
    total = 0
    for i, ch in enumerate(s):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return total % 10 == 0


def iban_ok(s: str) -> bool:
    s = s.replace(" ", "").upper()
    if len(s) != 24:
        return False
    r = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in r)
    return int(num) % 97 == 1


def mask(v: str) -> str:
    v = str(v)
    if len(v) <= 4:
        return "*" * len(v)
    if "@" in v:
        user, _, dom = v.partition("@")
        return user[:1] + "***@" + dom
    keep = 2 if len(v) > 8 else 1
    return v[:keep] + "*" * (len(v) - 2 * keep) + v[-keep:]


def value_detectors(series: pd.Series) -> dict[str, dict]:
    s = series.dropna().astype(str)
    if s.empty:
        return {}
    n = len(s)
    found: dict[str, dict] = {}

    def add(kind: str, hits: list[str]):
        if hits:
            found[kind] = {"rows_matched": len(hits), "ratio": round(len(hits) / n, 4),
                           "masked_samples": [mask(h) for h in hits[:3]]}

    ids, iqamas, mobiles, ibans, emails, hijri, ips = [], [], [], [], [], [], []
    arabic = 0
    for v in s:
        for m in SAUDI_ID_RE.findall(v):
            if luhn_ok(m):
                (ids if m[0] == "1" else iqamas).append(m)
        if MOBILE_RE.search(v):
            mobiles.append(MOBILE_RE.search(v).group(0))
        for m in IBAN_RE.findall(v):
            if iban_ok(m):
                ibans.append(m)
        if EMAIL_RE.search(v):
            emails.append(EMAIL_RE.search(v).group(0))
        if HIJRI_RE.search(v):
            hijri.append(HIJRI_RE.search(v).group(0))
        if IPV4_RE.search(v):
            ips.append(IPV4_RE.search(v).group(0))
        if ARABIC_RE.search(v):
            arabic += 1
    add("saudi_national_id", ids)
    add("iqama_number", iqamas)
    add("saudi_mobile", mobiles)
    add("saudi_iban", ibans)
    add("email", emails)
    add("hijri_date", hijri)
    add("ipv4", ips)
    # free text: long average length suggests notes that may hide names / medical details
    avg_len = s.str.len().mean()
    if avg_len > 40:
        found["free_text"] = {"avg_length": round(float(avg_len), 1), "arabic_ratio": round(arabic / n, 3),
                              "note": "review manually: free text can contain names, addresses, health details"}
    return found


def k_anonymity(df: pd.DataFrame, cols: list[str]) -> dict | None:
    cols = [c for c in cols if c in df.columns]
    if not cols:
        return None
    sizes = df[cols].astype(str).value_counts(dropna=False)
    return {"quasi_identifiers": cols, "k": int(sizes.min()), "groups": int(len(sizes)),
            "groups_below_5": int((sizes < 5).sum()), "rows_in_groups_below_5": int(sizes[sizes < 5].sum())}


def load(path: Path, nrows: int | None) -> pd.DataFrame:
    ext = path.suffix.lower()
    if ext in (".csv", ".txt"):
        return pd.read_csv(path, nrows=nrows, dtype=str, encoding_errors="replace")
    if ext == ".tsv":
        return pd.read_csv(path, sep="\t", nrows=nrows, dtype=str, encoding_errors="replace")
    if ext == ".parquet":
        df = pd.read_parquet(path)
        return df.head(nrows) if nrows else df
    if ext in (".jsonl", ".ndjson"):
        return pd.read_json(path, lines=True, nrows=nrows, dtype=False)
    if ext == ".json":
        df = pd.read_json(path, dtype=False)
        return df.head(nrows) if nrows else df
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, nrows=nrows, dtype=str)
    raise ValueError(f"unsupported file type: {ext}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset")
    ap.add_argument("--out", default="privacy_report.json")
    ap.add_argument("--sample-rows", type=int, default=50000, help="rows to scan (0 = all)")
    args = ap.parse_args()
    # Arabic column names must not crash a cp1252 Windows console
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    path = Path(args.dataset)
    if not path.is_file():
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    try:
        df = load(path, args.sample_rows or None)
    except Exception as e:  # noqa: BLE001
        print(f"error: could not read {path}: {e}", file=sys.stderr)
        return 2

    columns = {}
    direct, quasi, sensitive = [], [], []
    for col in df.columns:
        info: dict = {}
        d = match_hints(col, DIRECT_ID_HINTS)
        q = match_hints(col, QUASI_ID_HINTS)
        s = match_hints(col, SENSITIVE_HINTS)
        v = value_detectors(df[col])
        value_direct = {"saudi_national_id", "iqama_number", "saudi_mobile", "saudi_iban", "email", "ipv4"}
        if d or (set(v) & value_direct):
            direct.append(col)
            info["role"] = "direct_identifier"
        elif s:
            info["role"] = "sensitive"
        elif q or "hijri_date" in v:
            quasi.append(col)
            info["role"] = "quasi_identifier"
        elif "free_text" in v:
            info["role"] = "free_text_review"
        else:
            continue
        if s:
            sensitive.append(col)
            info["sensitive_categories"] = s
        if d:
            info["name_hints"] = d
        if q:
            info["quasi_hints"] = q
        if v:
            info["value_detections"] = v
        columns[str(col)] = info

    report = {
        "dataset": str(path),
        "sha256": sha256(path),
        "scanned_on": date.today().isoformat(),
        "rows_scanned": int(len(df)),
        "columns_total": int(df.shape[1]),
        "direct_identifiers": [str(c) for c in direct],
        "quasi_identifiers": [str(c) for c in quasi],
        "sensitive_columns": [str(c) for c in sensitive],
        "k_anonymity": k_anonymity(df, quasi),
        "columns": columns,
        "suggested_verdict": (
            "BLOCKED_PENDING_REVIEW" if sensitive else
            "CONDITIONAL" if (direct or quasi or any(c.get("role") == "free_text_review" for c in columns.values())) else
            "NO_PERSONAL_DATA_DETECTED"
        ),
        "note": "Automated scan; the steward must still confirm classification, legal basis and residency.",
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"dataset   : {path}  ({report['rows_scanned']} rows scanned, {report['columns_total']} columns)")
    print(f"sha256    : {report['sha256']}")
    print(f"direct ids: {', '.join(report['direct_identifiers']) or '-'}")
    print(f"quasi ids : {', '.join(report['quasi_identifiers']) or '-'}")
    print(f"sensitive : {', '.join(report['sensitive_columns']) or '-'}")
    if report["k_anonymity"]:
        k = report["k_anonymity"]
        print(f"k-anon    : k={k['k']} over {k['quasi_identifiers']} "
              f"({k['rows_in_groups_below_5']} rows in groups < 5)")
    for col, info in columns.items():
        if info.get("role") == "free_text_review":
            print(f"review    : '{col}' is free text - check manually")
    print(f"suggested : {report['suggested_verdict']}")
    print(f"report    : {args.out}")
    return 0 if report["suggested_verdict"] == "NO_PERSONAL_DATA_DETECTED" else 1


if __name__ == "__main__":
    sys.exit(main())
