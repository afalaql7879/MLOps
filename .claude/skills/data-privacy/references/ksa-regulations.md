# KSA data-protection reference for the data steward

Summary for engineering decisions, not legal advice. Check the current official texts on
sdaia.gov.sa, nca.gov.sa and the entity's own data management office before relying on a detail.

## Personal Data Protection Law (PDPL)

- Royal Decree M/19 (1443H), amended by Royal Decree M/148 (1444H). In force 14 Sep 2023, with a one-year
  compliance grace period to 14 Sep 2024. Regulator: SDAIA.
- Applies to processing of personal data of individuals in the Kingdom, by any party, including
  processing outside the Kingdom of data about residents.
- **Personal data**: any data that identifies or can identify a person directly or indirectly — name,
  ID numbers, addresses, contact numbers, licence and record numbers, bank/credit card numbers, photos,
  video, and other personal identifiers.
- **Sensitive data** (needs extra justification and protection): racial or ethnic origin; religious,
  intellectual or political belief; criminal and security data; biometric data; genetic data; health
  data; data indicating that one or both of a person's parents are unknown.
- **Principles that matter for model training**
  - Purpose limitation: data collected for one purpose may be reused only for a compatible purpose.
  - Minimisation: process only what is necessary for the purpose.
  - Accuracy, and destruction once the purpose is fulfilled (retention limits).
  - Data subject rights: access, correction, destruction. Keep a mapping that lets you honour deletion
    requests (e.g. retrain or document why the model is not affected).
  - Records of processing activities must be kept by the controller.
  - Breach notification to SDAIA within 72 hours of becoming aware of an incident that harms data
    subjects; notify affected individuals where required.
- **Cross-border transfer**: allowed only under the Regulation on Personal Data Transfer outside the
  Kingdom (adequacy, appropriate safeguards such as standard contractual clauses or binding corporate
  rules, or limited exceptions), after a risk assessment, and never where it harms national security or
  vital interests. Treat any non-KSA cloud region or external API as a transfer.
- Anonymised data (cannot be re-linked to a person by any reasonable means) is outside the PDPL;
  pseudonymised data is not.

## NDMO / SDAIA National Data Management and Personal Data Protection Standards

- Governance domains include data classification, personal data protection, data sharing, data quality,
  metadata, and data lifecycle. Government entities must have a Data Management Office and a
  Personal Data Protection officer (DPO); data stewards per domain.
- **Data Classification Policy levels**
  | Level | Meaning | Steward action |
  |---|---|---|
  | Top Secret | Exceptionally grave damage to national interests if disclosed | BLOCK; approved govt. environment only |
  | Secret | Grave damage to national interests | BLOCK; approved govt. environment only |
  | Restricted | Limited damage to entity/national interest, individuals | Proceed in KSA-hosted, controlled environment |
  | Public | No impact | Proceed; still check for personal data |
  - Data is classified by the owning entity; when unclassified, treat as `Restricted` until classified.
  - Personal data is not `Public` merely because it was published.
- Data sharing between government entities requires a data-sharing agreement and a stated purpose.

## NCA controls (cybersecurity)

- **ECC** (Essential Cybersecurity Controls) baseline for government entities and critical infrastructure.
- **DCC** (Data Cybersecurity Controls): protection by classification level — encryption at rest/in
  transit, access control, secure data disposal, data in test/dev environments must be masked.
  Applying it to ML: training environments are non-production, so personal data there should be masked
  or de-identified.
- **CCC** (Cloud Cybersecurity Controls): classified government data hosted only with providers and
  regions approved for that classification, inside the Kingdom.

## SDAIA AI Ethics Principles and GenAI guidelines

- AI Ethics Principles (2023): fairness, privacy and security, humanity, social and environmental
  benefit, reliability and safety, transparency and explainability, accountability and responsibility.
  Practical checks: bias across nationality, gender, region and age groups; document data provenance.
- Generative AI guidelines for government (2024): don't put classified or personal data into public GenAI
  tools; test outputs for leaked data; keep a human accountable.

## Saudi identifier formats (used by the scanner)

| Identifier | Format |
|---|---|
| National ID (citizen) | 10 digits, starts with `1`, Luhn-style check digit |
| Iqama (resident) | 10 digits, starts with `2`, same check digit |
| Mobile | `05XXXXXXXX`, `+9665XXXXXXXX`, `009665XXXXXXXX` |
| IBAN | `SA` + 22 digits (24 chars), mod-97 checksum |
| Hijri dates | `14XX/MM/DD`, often used for DOB in government records |

## De-identification techniques, from strongest to weakest

1. Drop the field.
2. Aggregate / generalise (age band, region, year only).
3. Suppress rare combinations (k-anonymity ≥ 5; ≥ 10 for health or small populations).
4. Pseudonymise with keyed HMAC (secret outside repo). Still personal data.
5. Masking (`1XXXXXX234`) — display only, not a training transform.
