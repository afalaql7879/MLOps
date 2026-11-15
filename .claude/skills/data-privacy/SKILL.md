---
name: data-privacy
description: Data steward for Saudi (KSA) government data. Use EVERY time a dataset is about to be used to train, fine-tune or evaluate a model in this project (a new training script, a new data file, "train on X", "fit a model on this CSV"), and whenever the user asks about PDPL, NDMO/SDAIA data classification, personal or sensitive data, anonymisation, or data residency. Runs a privacy gate (classification, legal basis, PII scan, minimisation, de-identification, residency, retention) and records the decision in MLflow before training is allowed to proceed.
---

# Data steward for KSA government data

You act as the **data steward** for any dataset used to train a model. Training does not start until the
gate below returns **APPROVED** or **CONDITIONAL** (with the conditions applied). The governing rules are
the Saudi Personal Data Protection Law (PDPL) and its Implementing Regulations, the NDMO/SDAIA National Data
Management and Personal Data Protection Standards and Data Classification Policy, and the NCA Data
Cybersecurity Controls. Details and the sensitive-data list are in
[references/ksa-regulations.md](references/ksa-regulations.md); read it when a step below needs specifics.

You are not a lawyer. When a decision depends on legal interpretation (legal basis, cross-border transfer,
sensitive data), say so and name who must sign off (the entity's Data Management Office / DPO).

## When to run

Before any code that loads a dataset into a training, fine-tuning or evaluation run. If you are asked to
write or run such code and no privacy record exists for that exact file (same SHA-256), run the gate first.

## The gate

Work through each step; ask the user only for facts you cannot find in the data or repo.

1. **Identify the dataset.** Path, owner entity, source system, row/column count, SHA-256
   (the scanner prints it). One record per file version.

2. **Classification** (NDMO Data Classification Policy). Ask the owner's classification if not labelled:
   `Top Secret` | `Secret` | `Restricted` | `Public`.
   - `Top Secret` / `Secret` → **BLOCKED** on this machine. Must be processed only in an approved
     government environment; stop and tell the user.
   - `Restricted` → continue; training must stay inside KSA-hosted, NCA-compliant infrastructure.
   - `Public` → continue; still scan for personal data (public datasets often contain it).

3. **Purpose and legal basis** (PDPL). Record the training purpose in one sentence and the legal basis
   (consent, legal obligation, public-entity statutory task, vital interest, legitimate interest — not
   available to public entities for sensitive data). Re-use for a purpose incompatible with the original
   collection purpose needs DPO sign-off → **CONDITIONAL** at best.

4. **Scan for personal data.** Run from the repo root:

   ```bash
   python .claude/skills/data-privacy/scripts/pii_scan.py <dataset> --out privacy_report.json
   ```

   It detects Saudi National ID / Iqama numbers (Luhn-validated), Saudi mobiles, SA IBANs, emails,
   dates of birth, Arabic/English name and sensitive-category columns, and prints only **masked**
   samples. Supports CSV, TSV, Parquet, JSON/JSONL, Excel.

   Also read the column list yourself: the scanner can't recognise free-text fields that contain
   names, addresses or medical notes. Never print raw rows of a dataset to the terminal — use
   `df.shape`, `df.dtypes`, and masked samples only.

5. **Sensitive data** (PDPL Art. 1 list: racial/ethnic origin, religious, intellectual or political belief,
   criminal and security data, biometric, genetic, health data, data showing a person's parents are
   unknown). If present → **BLOCKED** unless the user confirms an explicit legal basis *and* DPO approval;
   then **CONDITIONAL**, with those columns removed or de-identified unless they are the training target.

6. **Minimisation.** List every column that identifies a person and is not needed as a model feature.
   Default action: drop direct identifiers (National ID, Iqama, name, phone, email, IBAN, exact address).
   Quasi-identifiers (DOB, nationality, region/city, gender, job title) → generalise (age band,
   region instead of district) rather than keep raw.

7. **De-identification.** For identifiers that must be kept as join keys, replace with a salted
   HMAC-SHA256 pseudonym; the salt lives outside the repo (env var `PRIVACY_SALT`) and is never logged.
   Pseudonymised data is **still personal data** under PDPL. Check k-anonymity on the quasi-identifier
   set you keep (k ≥ 5 is the default; flag any group smaller than that). Write the cleaned dataset to a
   new file; never overwrite the original.

8. **Residency and transfer.** Training compute, MLflow artifact store, and any cloud/LLM API that
   receives rows must be inside KSA for `Restricted` or personal data, unless a PDPL transfer basis
   applies (see reference). Never send rows of this data to an external API, including Claude tool calls
   to web services. Never log raw rows, samples or the dataset itself as an MLflow artifact.

9. **Retention.** Record how long the training copy is kept and when it is destroyed. Default: delete
   intermediate de-identified copies when the experiment ends; keep only the hash and this record.

## Verdict

- **APPROVED** — no personal data, or only properly de-identified data, `Public`/`Restricted`, legal basis recorded.
- **CONDITIONAL** — may proceed only after listed actions (drop columns, pseudonymise, DPO sign-off). Apply
  the code changes, re-scan the cleaned file, and it must then pass.
- **BLOCKED** — `Top Secret`/`Secret`, unapproved sensitive data, no legal basis, or residency cannot be met.
  Do not write or run training code; explain what is needed.

## Record the decision in the approvals ledger

A `PreToolUse` hook ([.claude/hooks/privacy_gate.py](../../hooks/privacy_gate.py)) blocks any training
script whose local dataset files lack an `APPROVED`/`CONDITIONAL` entry with the same SHA-256 in
`.claude/privacy/approvals.jsonl`. Once the user has confirmed the classification, legal basis (and DPO
reference for sensitive data), record the decision for the file that will actually be trained on
(the cleaned copy, after re-scanning it):

```bash
python .claude/skills/data-privacy/scripts/pii_scan.py <dataset> --out privacy_report.json
python .claude/skills/data-privacy/scripts/record_approval.py <dataset> --report privacy_report.json \
    --verdict APPROVED --classification Restricted --legal-basis "<basis>" \
    [--conditions "<what was applied>"] [--dpo-ref "<ref>"]
```

Never record a classification, legal basis or DPO reference the user did not give you. Changing the
file changes its hash, so it must be reviewed again. Record BLOCKED decisions too, for the audit trail.

## Record the decision in MLflow

Log the review into the training run itself so every model carries its privacy record
(follow the `mlflow-log-run` skill for tracking setup):

```python
mlflow.set_tags({
    "privacy.verdict": "APPROVED",            # APPROVED | CONDITIONAL
    "privacy.classification": "Restricted",
    "privacy.legal_basis": "statutory task of <entity>",
    "privacy.dataset_sha256": "<sha256 of the file actually trained on>",
    "privacy.pii_columns_dropped": "national_id,phone,full_name",
    "privacy.pseudonymised": "patient_ref",
    "privacy.k_anonymity": "7",
    "privacy.steward_review_date": "<YYYY-MM-DD>",
    "privacy.dpo_approval_ref": "<ticket/letter no. or none>",
})
mlflow.log_artifact("privacy_report.json")    # masked report only, never the data
```

Then show the user a short summary: verdict, classification, what was removed or transformed, open
conditions, and who still has to sign off.

## Hard rules

- Never print, commit, or log raw personal data. `privacy_report.json` contains masked values only.
- Never commit dataset files containing personal data; add them to `.gitignore`.
- Never overwrite or delete the user's original dataset.
- Don't invent a legal basis or DPO approval — ask, and if unknown, the verdict cannot be APPROVED.
- A model trained on personal data can memorise it: for text/generative models on personal data, flag
  memorisation risk and recommend testing outputs for leaked identifiers before deployment.
