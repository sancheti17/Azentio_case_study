"""Offline labels-aware evaluation; labels never enter the extraction pipeline."""
from __future__ import annotations
import argparse, hashlib, json, re
from collections import Counter
from datetime import date
from pathlib import Path
from .config import DEFAULT_REVIEW_THRESHOLD, EVALUATION_AS_OF, TARGET_SILENT_ERROR_RATE, THRESHOLD_CANDIDATES
from .models import SCHEMAS
from .pipeline import Pipeline
from .providers import FakeProvider, HTTPProvider
from .rules import normalize

RULE_FAILURE_CODES = {"PAN_FORMAT_INVALID", "ID_CHECKSUM_FAILED", "PINCODE_INVALID", "DATE_INVALID", "GENDER_INVALID", "TEXT_OCR_SUSPECT"}

def _provider_factory(name):
    if name == "fake": return FakeProvider
    if name in {"openai", "ollama"}: return lambda: HTTPProvider(name)
    raise ValueError("provider must be fake, openai, or ollama")

def _labels(path):
    path = Path(path)
    if path.is_dir(): path /= "labels.jsonl"
    if not path.is_file(): raise FileNotFoundError(f"labels file not found: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows: raise ValueError("labels file contains no documents")
    return path, rows

def _canon(field, value):
    value, _ = normalize(field, value)
    if field in {"id_number", "pincode"}: value = re.sub(r"[^0-9]", "", value)
    return value

def _rate(correct, total): return round(correct / total, 6) if total else None

def run_evaluation(labels_path, *, provider="fake", threshold_candidates=THRESHOLD_CANDIDATES):
    """Read labels only in this evaluator; call Pipeline with OCR text only.

    Each document is extracted once at threshold zero. The same prediction/confidence is
    then replayed at each threshold, avoiding repeated real-provider calls and making the
    sweep deterministic. Complete normalized values are compared via private hashes.
    """
    labels, rows = _labels(labels_path)
    factory = _provider_factory(provider)
    thresholds = tuple(float(t) for t in threshold_candidates)
    if not thresholds or any(not 0 <= t <= 1 for t in thresholds): raise ValueError("thresholds must be in [0,1]")
    as_of = date.fromisoformat(EVALUATION_AS_OF)
    outcomes=[]; type_correct=0; type_counts=Counter()
    by_type={}; errors=Counter({k:0 for k in ("missing","wrong_value","rule_failed","provider_failed")})
    total_errors=0
    for row in rows:
        kind=row["doc_type"]
        if kind not in SCHEMAS: raise ValueError("unknown labeled doc_type")
        text=(labels.parent/(row["doc_id"]+".txt")).read_text(encoding="utf-8")
        hashes={}
        result=Pipeline(factory(),threshold=0.0,today=as_of).extract(row["doc_id"],text,_field_hashes=hashes)
        type_ok=(result.doc_type==kind); type_correct+=int(type_ok); type_counts[kind]+=1
        correct_map={}
        for field, truth in row["fields"].items():
            digest=hashlib.sha256(_canon(field,truth).encode("utf-8")).hexdigest()
            correct=hashes.get(field)==digest; correct_map[field]=correct
            stats=by_type.setdefault(kind,{}).setdefault(field,{"correct":0,"total":0})
            stats["total"]+=1; stats["correct"]+=int(correct); total_errors+=int(not correct)
            if not correct:
                prediction=result.fields.get(field); reasons=set(prediction.reason_codes) if prediction else set()
                if result.status=="failed" and (result.error or "").startswith("EXTRACTION_FAILED_AFTER_RETRY"): cat="provider_failed"
                elif prediction is None or field not in hashes: cat="missing"
                elif reasons & RULE_FAILURE_CODES: cat="rule_failed"
                else: cat="wrong_value"
                errors[cat]+=1
        outcomes.append({"row":row,"result":result,"correct":correct_map})

    total_fields=sum(x["total"] for fields in by_type.values() for x in fields.values())
    total_correct=sum(x["correct"] for fields in by_type.values() for x in fields.values())
    field_accuracy={}
    for kind in ("pan","id_card","address_proof"):
        field_accuracy[kind]={f:{**v,"accuracy":_rate(v["correct"],v["total"])} for f,v in by_type.get(kind,{}).items()}
    field_accuracy["overall"]={"correct":total_correct,"total":total_fields,"accuracy":_rate(total_correct,total_fields)}

    sweep=[]
    for threshold in thresholds:
        reviewed_fields=accepted_fields=silent_errors=reviewed_docs=accepted_docs=accepted_docs_with_errors=0
        status_counts=Counter()
        for item in outcomes:
            result=item["result"]; n=len(item["row"]["fields"])
            # Human review receives the whole document if any hard gate fails or any score is low.
            review=(result.status!="ok" or any(f.confidence < threshold for f in result.fields.values()))
            status="failed" if result.status=="failed" else ("needs_review" if review else "ok")
            status_counts[status]+=1
            if review: reviewed_fields+=n; reviewed_docs+=1
            else:
                accepted_fields+=n; accepted_docs+=1
                bad=sum(not ok for ok in item["correct"].values())
                silent_errors+=bad; accepted_docs_with_errors+=int(bad>0)
        sweep.append({"threshold":threshold,"review_rate":round(reviewed_fields/total_fields,6) if total_fields else None,
            "reviewed_fields":reviewed_fields,"total_fields":total_fields,"auto_accepted_fields":accepted_fields,
            "silent_errors":silent_errors,"silent_error_rate":round(silent_errors/accepted_fields,6) if accepted_fields else None,
            "reviewed_documents":reviewed_docs,"auto_accepted_documents":accepted_docs,"failed_documents":status_counts["failed"],
            "auto_accepted_documents_with_errors":accepted_docs_with_errors})
    qualified=[x for x in sweep if x["silent_error_rate"] is not None and x["silent_error_rate"]<=TARGET_SILENT_ERROR_RATE]
    target_met=bool(qualified); chosen=qualified[0]["threshold"] if target_met else max(thresholds)
    note="lowest tested threshold at or below 2% silent errors" if target_met else "no tested threshold meets 2%; highest tested threshold selected as conservative fallback"
    selected=next(x for x in sweep if x["threshold"]==chosen)
    flat={}
    for kind in ("pan","id_card","address_proof"):
        for field,metric in field_accuracy[kind].items(): flat[f"{kind}.{field}"]={**metric,"accepted_total":0,"accepted_errors":0}
    for item in outcomes:
        result=item["result"]
        if result.status!="ok" or any(f.confidence < chosen for f in result.fields.values()): continue
        for field,ok in item["correct"].items():
            v=flat[item["row"]["doc_type"]+"."+field]; v["accepted_total"]+=1; v["accepted_errors"]+=int(not ok)
    statuses={"ok":selected["auto_accepted_documents"],"needs_review":selected["reviewed_documents"]-selected["failed_documents"],"failed":selected["failed_documents"]}
    return {"provider":"FakeProvider" if provider=="fake" else provider,"evaluation_as_of":EVALUATION_AS_OF,
        "documents":len(rows),"applicants":len({r.get("applicant_id") for r in rows}),"noisy_documents":sum(bool(r.get("noisy",False)) for r in rows),
        "doc_type_accuracy":{"correct":type_correct,"total":len(rows),"accuracy":_rate(type_correct,len(rows))},
        "field_accuracy":field_accuracy,"overall_field_accuracy":field_accuracy["overall"]["accuracy"],
        "error_breakdown":{**{k:errors[k] for k in ("missing","wrong_value","rule_failed","provider_failed")},"total_field_errors":total_errors},
        "threshold_sweep":sweep,"chosen_threshold":chosen,"target_silent_error_rate":TARGET_SILENT_ERROR_RATE,
        "chosen_threshold_target_met":target_met,"threshold_selection_note":note,
        "fields":flat,"statuses":statuses,"threshold":chosen,"calibrated":False,"all_field_errors":total_errors,
        "accepted_fields":selected["auto_accepted_fields"],"accepted_field_errors":selected["silent_errors"],
        "accepted_field_error_rate":selected["silent_error_rate"],"accepted_document_errors":selected["auto_accepted_documents_with_errors"],
        "accepted_document_error_rate":round(selected["auto_accepted_documents_with_errors"]/selected["auto_accepted_documents"],6) if selected["auto_accepted_documents"] else None,
        "limitations":["Synthetic shared-template development data, not held-out calibration or population risk evidence.",
          "Every field in a needs_review/failed document counts as reviewed; silent-error denominator is auto-accepted fields.",
          "IDs are compared through private in-memory hashes; aggregate reports contain no extracted values.",
          "An ok extraction is not identity authentication, regulatory KYC, account approval, or credit approval."]}

def render_markdown(r):
    lines=["# Synthetic KYC extraction evaluation","",f"- Provider: **{r['provider']}**; evaluation as-of {r['evaluation_as_of']}.",
      f"- Dataset: **{r['documents']} documents**, {r['applicants']} applicants, {r['noisy_documents']} noisy.",
      f"- Document-type accuracy: **{r['doc_type_accuracy']['correct']}/{r['doc_type_accuracy']['total']} ({r['doc_type_accuracy']['accuracy']:.2%})**.",
      f"- Overall field accuracy: **{r['field_accuracy']['overall']['correct']}/{r['field_accuracy']['overall']['total']} ({r['overall_field_accuracy']:.2%})**.","",
      "## Per-field accuracy","","| Document type | Field | Correct / total | Accuracy |","|---|---|---:|---:|"]
    for kind in ("pan","id_card","address_proof"):
        for field,m in r["field_accuracy"][kind].items(): lines.append(f"| {kind} | {field} | {m['correct']}/{m['total']} | {m['accuracy']:.2%} |")
    lines += ["","## Error breakdown","","| Category | Count |","|---|---:|"]
    for k in ("missing","wrong_value","rule_failed","provider_failed"): lines.append(f"| {k} | {r['error_breakdown'][k]} |")
    lines += [f"| **Total field errors** | **{r['error_breakdown']['total_field_errors']}** |","","## Threshold sweep","",
       "Review rate counts every field in a document routed to review; silent-error rate is wrong auto-accepted fields divided by all auto-accepted fields.",
       "| Threshold | Review rate | Reviewed fields / total | Auto-accepted fields | Silent errors | Silent-error rate |","|---:|---:|---:|---:|---:|---:|"]
    for x in r["threshold_sweep"]:
        rate="n/a" if x["silent_error_rate"] is None else f"{x['silent_error_rate']:.2%}"
        lines.append(f"| {x['threshold']:.2f} | {x['review_rate']:.2%} | {x['reviewed_fields']}/{x['total_fields']} | {x['auto_accepted_fields']} | {x['silent_errors']} | {rate} |")
    if r["chosen_threshold_target_met"]:
        x=next(x for x in r["threshold_sweep"] if x["threshold"]==r["chosen_threshold"])
        lines += ["",f"**Chosen default: {r['chosen_threshold']:.2f}.** It is the lowest tested threshold with silent-error rate <=2%; it routes {x['review_rate']:.2%} of fields to review. The trade-off is more review as the threshold rises; hard safety gates route flagged documents independently of the score."]
    else: lines += ["",f"**No tested threshold achieves the <=2% silent-error target.** The configured {r['chosen_threshold']:.2f} is the highest tested value as a conservative fallback; it does not meet the target."]
    lines += ["","## Interpretation","","Results are synthetic development measurements, not held-out calibration or bank risk evidence. The fake provider is an offline OCR parser, not an identity verifier; no real provider was run.",""]
    return "\n".join(lines)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--labels",type=Path,default=Path("data/labels.jsonl"))
    p.add_argument("--provider",choices=("fake","openai","ollama"),default="fake"); p.add_argument("--out",type=Path,default=Path("reports/")); a=p.parse_args()
    r=run_evaluation(a.labels,provider=a.provider); a.out.mkdir(parents=True,exist_ok=True)
    (a.out/"eval.json").write_text(json.dumps(r,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    (a.out/"eval.md").write_text(render_markdown(r),encoding="utf-8")
    print(json.dumps({k:r[k] for k in ("doc_type_accuracy","overall_field_accuracy","error_breakdown","threshold_sweep","chosen_threshold","chosen_threshold_target_met")},indent=2,sort_keys=True))
if __name__=="__main__": main()
