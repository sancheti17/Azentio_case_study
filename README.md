# Azentio_case_study
Build an LLM-backed eKYC pipeline that extracts and validates identity fields from KYC documents, measures its own field-level accuracy, and routes uncertain cases to a human reviewer.
# SPECIMEN KYC — offline-first extraction demonstration

Python 3.10+ package `kyc` for one-document OCR extraction. No real people or identity records are used. **`ok` means the extraction gate passed; it is not identity authentication, regulatory KYC approval, account approval, or a loan decision.** This Task 1 implementation is not production-ready or a substitute for Task 2 calibration.

## Setup and commands

Create a virtual environment, then install the package and test dependencies:

```sh
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[test]'
python3.12 -m venv /tmp/kyc-venv312
source /tmp/kyc-venv312/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
python -m kyc.generate --applicants 30 --seed 42 --out new-data/
```

Generation refuses a nonempty output directory to prevent overwrites or stale files; use another empty path for each run. If the distribution includes sample `data/`, use `--out new-data/`.

POST `http://127.0.0.1:8000/v1/documents/extract` with JSON `{ "doc_id": "application-1", "text": "<OCR text>" }`. OpenAPI is at `/docs`. The response has `doc_id`, `doc_type`, `status` (`ok`, `needs_review`, `failed`), and `fields`; each field has `value`, `confidence`, `reason_codes`. Failed responses also contain a safe `error`. Unknown document type has JSON `doc_type: null`, and failed responses always retain an empty `fields` object. No original ID is returned: a valid-shaped ID is `XXXXXXXX` plus its final four digits; invalid-shaped IDs are completely masked. CLI emits the same JSON and exits 0 for ok/review, 1 for failed, 2 for input/configuration failure. CLI defaults doc_id to the filename stem.

The API writes SQLite audit decisions to `kyc-audit.sqlite3` by default. Configure `KYC_AUDIT_DB` and `KYC_THRESHOLD` for the API; the CLI uses `--audit-db` and `--threshold`. Threshold defaults to **0.8**. Library usage is `Pipeline(FakeProvider(), threshold=0.8, audit=AuditStore('decisions.sqlite3')).extract(doc_id, text)`. Tests use isolated temporary paths, no environment variables, and block outbound IPv4/IPv6 socket connections. Installing dependencies may require a one-time download; running the fake, generator, tests and evaluation requires no service or network.

## Structure and design

- `generate.py`: local seeded RNG, fixed date ranges, stable order/UTF-8/LF/JSON serialization. 30 applicants produce 90 documents and `labels.jsonl`; exactly `ceil(40% * documents)` are noisy. Every text includes `SPECIMEN`; names are artificial `SPECIMEN NEXORA ...` tokens. IDs have 11-digit prefixes beginning 0/1 plus a computed Verhoeff check digit. No real-world identifiers were sourced. PAN values are synthetic, format-valid test strings, not authenticated or guaranteed unassigned PANs.
- Noise includes O/0 or I/1 swaps, shuffled lines, extra headers/footers, and full dates in ISO/day-first numeric/English-month forms. Text-field confusables are intentionally sometimes irrecoverable. Ground truth remains uncorrupted in labels. Clean and noisy ground truth is generated together but never read by extraction.
- `source.py`: conservative keyword classifier, rejecting unknown/mixed document types; line-label evidence extraction with common synonyms. A document with insufficient recognizable evidence must go to review or fail, not be guessed.
- `providers.py`: `LLMProvider` protocol and deterministic lexical `FakeProvider`. The fake reads only the supplied OCR, not labels, file names, seeds, or the filesystem. Fault injection: `FakeProvider(['malformed'])`, `['missing_field']`, `['malformed','malformed']`, `['raise']`.
- `models.py`: Pydantic v2 strict string schemas for PAN, ID card and address proof, no extra keys/coercion. All fields are mandatory and nonempty. Dates enter as strict text so an impossible date can return a per-field `DATE_INVALID`, not disappear in a schema failure. Semantic rules then normalize valid full dates to ISO.
- `pipeline.py`: classify → provider → strict JSON parse → schema validate → normalize/rules/evidence/confidence → masked public result. Duplicate keys, fenced JSON, NaN, wrong types, extra/missing fields and oversized outputs are rejected. Exactly one repair attempt after JSON/schema/provider failure; the repair prompt includes safe error categories/field names, never raw exception input. Two failed attempts return `failed` with empty fields. Rule violations return `needs_review`, without unnecessary LLM retries.
- `audit.py`: stdlib SQLite, one short-lived connection per operation for FastAPI thread safety. Every configured request stores UTC time, source SHA-256, provider class, version, threshold, decision date, attempt count, safe retry events, and the **masked** decision JSON. No raw OCR/provider output. Audit write failure returns `failed`, never `ok`. API/CLI always configure auditing; direct library callers must supply a store if they require persistence. This local audit is not tamper-evident immutable storage; hashing does not anonymize PII or prove authenticity.

No external database, broker, paid API, fuzzy-matching dependency, date parsing service, or package-install side effect is required at runtime. The core is synchronous: suitable for a prototype, not an unbounded production workload. FastAPI executes the sync route in a threadpool.

## Rules and normalization

| Field | Check / failure reason |
|---|---|
| pan_number | ASCII `^[A-Z]{5}[0-9]{4}[A-Z]$` and fourth character `P`; `PAN_FORMAT_INVALID` |
| id_number | Exactly 12 ASCII digits and Verhoeff; `ID_CHECKSUM_FAILED` |
| pincode | Six ASCII digits, first not 0; `PINCODE_INVALID` |
| dob, issue_date | Complete parseable date; no future date relative to IST; `DATE_INVALID` |
| gender | MALE, FEMALE, OTHER, with M/F normalization; `GENDER_INVALID` |
| name, father_name, address | Digits in names or letter-[0/1]-letter runs in addresses flag `TEXT_OCR_SUSPECT`; review, never guessed correction |

Dates: `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-Mon-YYYY`, `DD-MM-YYYY`. `12/03/1991` is **12 March**, not December 3. English month parsing is locale independent. Incomplete/relative dates are not inferred. Numeric identifier positions may repair O→0/I→1 (and lowercase OCR equivalents); PAN letter positions repair 0→O/1→I. Every such repair is flagged `OCR_CORRECTED` and forces human review. No fuzzy name or address correction is attempted. Whitespace/case/separator normalization alone is not an OCR correction. Checksums/format do not verify issuance, ownership, address existence, age eligibility or fraud.

## Confidence and review policy

For each field define:

- **R** = 1 if its deterministic rules pass, else 0. Nonempty names/addresses have only structural and conspicuous-text-anomaly checks, not authenticity checks.
- **S** = 1 if the canonical value equals at least one canonical source value under a recognized field label, else 0. This is field-specific, not a global substring search.
- **C** = 1 if a confusable correction was needed in the extracted value or the matching source evidence, else 0.

`confidence = round(clamp(0.55*R + 0.45*S - 0.20*C, 0, 1), 4)`.

A rule failure or conflicting values under the same field label caps confidence at 0.49. Other reasons are `SOURCE_NOT_FOUND`, `SOURCE_CONFLICT`, `OCR_CORRECTED`, `LOW_CONFIDENCE`. No model self-reported confidence is used. **These scores are transparent heuristics, not calibrated probabilities.** Clean rule-passing/source-supported fields score 1.0; repaired ones 0.8; unsupported rule-passing values 0.55. At the default 0.8 threshold, equality alone is not low confidence (comparison is strict `<`), but repairs always trigger review.

A document is `needs_review` if any field fails a rule, is below threshold, lacks source support, contains an OCR correction, or has conflicting source evidence. These extra hard gates cannot be waived by setting the threshold to zero. Unknown/ambiguous classification, unrepairable model output and audit failure are `failed`, also requiring operational review. Never equate `failed` with a rejected identity.

## Optional real providers — explicit opt-in only

Default (including all tests): `KYC_PROVIDER` unset or `fake`. `get_provider()` reads configuration at construction time, never at import. Fake extraction has no network code.

For an OpenAI-compatible deployment set `KYC_PROVIDER=openai`, `KYC_MODEL=<your model>`, `KYC_BASE_URL=<base URL ending in /v1>`, and `KYC_API_KEY=<key if needed>`. The transport calls `/chat/completions`; API-key and response logs are not emitted. Default base URL is OpenAI's API. This option may incur costs and sends unmasked OCR/PII to that destination; do not enable it without bank approval.

For already-installed local Ollama, set `KYC_PROVIDER=ollama`, `KYC_MODEL=llama3.2`, optionally `KYC_BASE_URL=http://127.0.0.1:11434`. Calls use `/api/chat`, nonstreaming JSON, temperature zero, a 30-second timeout and bounded response size. Loading a local model is an optional separate setup; no model download is needed by tests. Optional real backends are not covered by the offline test results.

## Evaluation and results

```sh
python -m kyc.evaluate --data data/ --threshold 0.8 --out reports/evaluation.json
```

The report includes accuracy numerator/denominator per document-type/field, accepted-field errors, accepted-document errors, review counts and explicit accepted-field/document error-rate denominators. **Complete canonical IDs are compared**, not only their last four digits: the evaluator uses a private in-memory field-hash hook from the exact pipeline run, compares hashes with canonical labels, then discards them. Neither raw identity values nor hashes are written to reports. HTTP/CLI/audit remain masked. This is evaluation-only access to the synthetic ground truth; the provider itself never reads labels.

Verified on Linux/CPython 3.13: **64 tests passed**, including network-blocked FakeProvider paths, retry failures, all schemas, date/rule cases, byte-identical generation, CLI, API masking and SQLite auditing. See `reports/pytest.txt`, `reports/verification.json` and `reports/evaluation.json`.

With `--applicants 30 --seed 42` and threshold **0.8**:

| Document type / field | Exact canonical correctness | Accuracy | Errors accepted without review |
|---|---:|---:|---:|
| pan.name | 27/30 | 90.00% | 0 |
| pan.father_name | 30/30 | 100.00% | 0 |
| pan.dob | 30/30 | 100.00% | 0 |
| pan.pan_number | 30/30 | 100.00% | 0 |
| id_card.name | 29/30 | 96.67% | 0 |
| id_card.dob | 30/30 | 100.00% | 0 |
| id_card.gender | 30/30 | 100.00% | 0 |
| id_card.id_number (complete value) | 30/30 | 100.00% | 0 |
| id_card.address | 30/30 | 100.00% | 0 |
| id_card.pincode | 30/30 | 100.00% | 0 |
| address_proof.name | 28/30 | 93.33% | 0 |
| address_proof.address | 25/30 | 83.33% | 0 |
| address_proof.pincode | 30/30 | 100.00% | 0 |
| address_proof.issue_date | 30/30 | 100.00% | 0 |

- **90 documents**, 36 noisy (40%); 420 total fields; 409/420 exact (97.38%).
- **54/90 `ok` (60% coverage)**, 36/90 `needs_review` (40%), zero failed.
- **0/54 accepted documents with an observed field error**, and **0/260 accepted-field errors**. All 11 observed field errors are among reviewed documents. 26 reviewed documents had no final field error: conservative review includes correctly repaired OCR.
- The illustrative one-sided 95% zero-error binomial upper bound for 0/54 is **5.40%**, not 0%. Even this bound is not a valid real-bank population guarantee: documents are correlated by applicant, data is synthetic and this is a development set.
- Thresholds 0, 0.8, 0.9 and 1.0 gave the same decisions (`reports/threshold-sweep.json`): clean fields score 1.0 and extra safety gates dominate this dataset. Thus no numerical threshold has been calibrated or optimized here; 0.8 remains the specified provisional default.

**Development transparency:** the initial in-sample run, before generic suspicious-text review, had 61 accepted documents and 7 accepted documents containing 8 erroneous fields. `reports/development-baseline.json` preserves those counts. This exposed a weakness in relying on source matching. Generic name/address anomaly gates were added and retested, producing the final results above. Because the same development set informed that change, the improved results are **not held-out validation**. The baseline is historical evidence, not a report reproducible from the final rule version.

The default threshold remains **0.8 and uncalibrated**, not a threshold selected to meet a bank risk target. The synthetic generator and fake share simple label conventions, so even 100% on this set would not establish real-world accuracy. Source-supported text errors with no conspicuous anomaly can still confidently escape (for example an all-letter substitution in a name). A regression test deliberately demonstrates this limitation. Never infer identity correctness from source agreement or a passing format rule.

Before a bank enables straight-through onboarding, Task 2 must add independently generated/held-out, applicant-disjoint challenge data; predeclared risk targets and confidence intervals; precision/coverage vs threshold; name/DOB/address comparisons across all documents; identity-authenticity checks; calibrated confidence for the actual production provider; and routing/appeal controls. This single-document Task 1 API does **not** perform applicant-bundle matching or certify regulatory compliance. The small sample and correlated documents are not independent estimates of fraud risk; any zero-error binomial bound printed is illustrative only.

## Fully offline distribution option

The downloadable repository archive also contains an ignored `wheelhouse/` cache of dependencies plus `wheelhouse/SHA256SUMS`. This cache is for the **verified Linux x86-64 CPython 3.13** environment only (some wheels are interpreter/platform-specific); the package source supports Python 3.10+, but other versions/platforms need their own matching wheels. The cache is a distribution convenience, not part of the Git source history.

From the extracted repository root, in a compatible virtual environment:

```sh
python -m pip install --no-index --find-links wheelhouse fastapi==0.115.12 pydantic==2.11.3 uvicorn==0.34.2 pytest==8.3.5 httpx==0.28.1
python -m pytest -q
python -m kyc.generate --applicants 30 --seed 42 --out new-data/
python -m kyc.extract new-data/app_0001_pan.txt
```

When running from the repository root, no editable installation is necessary. For a normal installed package on another environment use the setup command above; dependency/build-tool installation is the only potentially network-requiring step, not tests or fake extraction. The optional real-provider path is never selected implicitly.

## Privacy and production boundaries

No application logger records OCR, full IDs, provider responses, retry values or credentials. API validation errors are replaced by a safe error instead of FastAPI's usual echoed inputs. IDs are masked before all result serialization and audit storage, including defensive redaction in wrong fields/doc IDs. Do not enable reverse-proxy body logging or provider HTTP debug tracing. The authorized synthetic files/labels necessarily contain synthetic full IDs; these are test fixtures, not application logs. Other KYC fields remain sensitive: this prototype does not supply encryption at rest, access control, retention enforcement, immutable audit storage, rate limits, upload limits at the gateway, TLS deployment, production observability, or a human-review UI. Keep it bound to localhost and use synthetic data only.
