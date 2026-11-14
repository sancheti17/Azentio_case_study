# Synthetic KYC extraction evaluation

- Provider: **FakeProvider**; evaluation as-of 2026-11-14.
- Dataset: **90 documents**, 30 applicants, 36 noisy.
- Document-type accuracy: **90/90 (100.00%)**.
- Overall field accuracy: **415/420 (98.81%)**.

## Per-field accuracy

| Document type | Field | Correct / total | Accuracy |
|---|---|---:|---:|
| pan | dob | 30/30 | 100.00% |
| pan | father_name | 30/30 | 100.00% |
| pan | name | 27/30 | 90.00% |
| pan | pan_number | 30/30 | 100.00% |
| id_card | address | 30/30 | 100.00% |
| id_card | dob | 30/30 | 100.00% |
| id_card | gender | 30/30 | 100.00% |
| id_card | id_number | 30/30 | 100.00% |
| id_card | name | 29/30 | 96.67% |
| id_card | pincode | 30/30 | 100.00% |
| address_proof | address | 30/30 | 100.00% |
| address_proof | issue_date | 30/30 | 100.00% |
| address_proof | name | 29/30 | 96.67% |
| address_proof | pincode | 30/30 | 100.00% |

## Error breakdown

| Category | Count |
|---|---:|
| missing | 0 |
| wrong_value | 0 |
| rule_failed | 5 |
| provider_failed | 0 |
| **Total field errors** | **5** |

## Threshold sweep

Review rate counts every field in a document routed to review; silent-error rate is wrong auto-accepted fields divided by all auto-accepted fields.
| Threshold | Review rate | Reviewed fields / total | Auto-accepted fields | Silent errors | Silent-error rate |
|---:|---:|---:|---:|---:|---:|
| 0.50 | 33.33% | 140/420 | 280 | 0 | 0.00% |
| 0.60 | 33.33% | 140/420 | 280 | 0 | 0.00% |
| 0.70 | 33.33% | 140/420 | 280 | 0 | 0.00% |
| 0.80 | 33.33% | 140/420 | 280 | 0 | 0.00% |
| 0.90 | 33.33% | 140/420 | 280 | 0 | 0.00% |
| 0.95 | 33.33% | 140/420 | 280 | 0 | 0.00% |

**Chosen default: 0.50.** It is the lowest tested threshold with silent-error rate <=2%; it routes 33.33% of fields to review. The trade-off is more review as the threshold rises; hard safety gates route flagged documents independently of the score.

## Interpretation

Results are synthetic development measurements, not held-out calibration or bank risk evidence. The fake provider is an offline OCR parser, not an identity verifier; no real provider was run.
