"""Historical-only publisher semantics for complete native binding learning.

Read original 2023 context/unit declarations after public inputs were frozen.
Do not convert a serialized publisher period into a visible date by guessing.
Unit, period and dimension references remain teacher-only, and cannot certify
visible header ownership. No later-year or independent-test labels are read.
"""

from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re

from lxml import etree

from native_correspondence_prepare_v10 import DEST as NATIVE
from native_context_crops_v12 import DEST as CONTEXT
from prepare import EXP, OUT, save, sha


DEST = NATIVE / "full_binding_preparation_v14"
XI = "{http://www.xbrl.org/2003/instance}"
IX = "{http://www.xbrl.org/2013/inlineXBRL}"
XD = "{http://xbrl.org/2006/xbrldi}"


def audit_file_for(source):
    if date.fromisoformat(source["period_end"]).year != 2023:
        raise ValueError("Only historical 2023 source annotations are allowed")
    return (OUT / "expanded_development_sources/full_publisher_reference_view_v2/scoring_only"
            / (source["lei"] + "_" + source["period_end"] + ".json"))


def expanded(value, namespaces):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing QName")
    value = value.strip()
    prefix, sep, local = value.partition(":")
    namespace = namespaces.get(prefix if sep else None)
    local = local if sep else prefix
    if not namespace or not local or ":" in local:
        raise ValueError("Unresolved QName")
    return "{" + namespace + "}" + local


def source_date(value):
    if not isinstance(value, str):
        raise ValueError("Missing source period date")
    value = value.strip()
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})?", value)
    if match:
        day = date.fromisoformat(match[1]).isoformat()
        offset = match[2] or ""
        if offset:
            datetime.fromisoformat(day + "T00:00:00" + offset.replace("Z", "+00:00"))
        return day + offset
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()


def period(node):
    children = list(node)
    if len(children) == 1 and children[0].tag == XI + "instant":
        return {"kind": "instant", "instant": source_date(children[0].text)}
    if len(children) == 1 and children[0].tag == XI + "forever":
        return {"kind": "forever"}
    if len(children) == 2 and {c.tag for c in children} == {XI + "startDate", XI + "endDate"}:
        dates = {c.tag: source_date(c.text) for c in children}
        start, end = dates[XI + "startDate"], dates[XI + "endDate"]
        # Compare source calendar dates, without inventing missing timezones.
        if start[:10] > end[:10]:
            raise ValueError("Inverted declared source period")
        return {"kind": "duration", "start_date": start, "end_date": end}
    raise ValueError("Unsupported or ambiguous declared source period")


def unit(node):
    if list(node.iter(XI + "divide")):
        divide = node.find(XI + "divide")
        if divide is None or len(node) != 1:
            raise ValueError("Ambiguous divided unit")
        numerator = divide.find(XI + "unitNumerator")
        denominator = divide.find(XI + "unitDenominator")
        if numerator is None or denominator is None or len(divide) != 2:
            raise ValueError("Missing divided-unit components")
        upper, lower = list(numerator), list(denominator)
    else:
        upper, lower = list(node), []
    if not upper or any(c.tag != XI + "measure" for c in upper + lower) or (lower == [] and node.find(XI + "divide") is not None):
        raise ValueError("Unsupported declared source unit")
    return {"numerator": sorted(expanded(c.text, c.nsmap) for c in upper),
            "denominator": sorted(expanded(c.text, c.nsmap) for c in lower)}


def context(node):
    entities = node.findall(XI + "entity/" + XI + "identifier")
    periods = node.findall(XI + "period")
    if len(entities) != 1 or len(periods) != 1 or not entities[0].text or not entities[0].get("scheme"):
        raise ValueError("Unsupported declared context identity or period")
    dimensions = []
    axes = set()
    for member in node.iter():
        if member.tag not in (XD + "explicitMember", XD + "typedMember"):
            continue
        axis = expanded(member.get("dimension"), member.nsmap)
        if axis in axes:
            raise ValueError("Duplicate declared dimension axis")
        axes.add(axis)
        if member.tag == XD + "explicitMember":
            value = {"kind": "explicit", "member": expanded(member.text, member.nsmap)}
        else:
            if len(member) != 1:
                raise ValueError("Ambiguous typed member")
            value = {"kind": "typed", "source_xml": etree.tostring(member[0], method="c14n").decode()}
        dimensions.append({"axis": axis, **value})
    return {"entity": {"scheme": entities[0].get("scheme"), "identifier": entities[0].text.strip()},
            "period": period(periods[0]), "dimensions": sorted(dimensions, key=lambda d: d["axis"])}


def unique_ids(nodes):
    result = {}
    for node in nodes:
        ident = node.get("id")
        if not ident or ident in result:
            raise ValueError("Missing or duplicate source declaration identifier")
        result[ident] = node
    return result


def prepare():
    manifest_file = DEST / "manifest.json"
    if manifest_file.exists():
        raise RuntimeError("Historical full-binding preparation already frozen")
    public_file = CONTEXT / "public/tasks.json"
    tasks = [t for t in json.loads(public_file.read_text()) if t["source_year"] == 2023]
    feedback_file = NATIVE / "training_only/past_feedback.json"
    feedback = json.loads(feedback_file.read_text())
    if len(tasks) != 280 or {t["task_id"] for t in tasks} != set(feedback):
        raise ValueError("Frozen historical target inventory changed")
    plan_file = EXP / "runs/visual_scope_relations_v8_20261009/scope_boundary_development_v1/download_plan.json"
    sources = [r for r in json.loads(plan_file.read_text())["files"]
               if r["kind"] == "native_report" and r["period_end"].startswith("2023-")]
    counts, records, hashes = Counter(), {}, {}
    for source in sorted(sources, key=lambda r: r["lei"]):
        company_tasks = [t for t in tasks if t["company"] == source["lei"]]
        if not company_tasks:
            continue
        counts["historical_report_files_read"] += 1
        path = Path(source["path"])
        tree = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True,
                                                      load_dtd=False, huge_tree=True))
        if tree.docinfo.doctype:
            raise ValueError("Source report contains a document type declaration")
        contexts = unique_ids(tree.iter(XI + "context"))
        units = unique_ids(tree.iter(XI + "unit"))
        audit_file = audit_file_for(source)
        audit_hash = sha(audit_file)
        audit = json.loads(audit_file.read_text())
        rows = {r["region_id"]: r for r in audit["rows"] if r.get("region_id")}
        source_hash = sha(path)
        if source_hash != audit["xhtml_sha256"]:
            raise ValueError("Original historical report differs from its annotation source")
        hashes[str(path)] = source_hash
        hashes[str(audit_file)] = audit_hash
        for task in company_tasks:
            ident, old = task["task_id"], feedback[task["task_id"]]
            if old["source_year"] != 2023 or old["source_audit_sha256"] != audit_hash:
                raise ValueError("Historical annotation source changed")
            row = rows[old["publisher_region_id"]]
            record = {**old, "teacher_binding": None, "teacher_status": "unknown",
                      "header_ownership_remains_uncertified": True,
                      "publication_entity_template_independence_not_certified": True,
                      "source_report_sha256": source_hash}
            try:
                declaration = expanded(row["concept"], tree.getroot().nsmap)
                expected = "{" + old["field_namespace"] + "}" + old["field_name"]
                if declaration != expected:
                    raise ValueError("Field annotation disagrees with native declaration")
                typed = context(contexts[row["context_ref"]])
                record["teacher_binding"] = {
                    "field": declaration, **typed,
                    "unit": unit(units[row["unit_ref"]]),
                    "quantity": old["canonical_value"],
                }
                record["teacher_status"] = "publisher_semantic_tuple_available"
                counts["publisher_semantic_tuple_available"] += 1
                counts["period_" + typed["period"]["kind"]] += 1
                counts["nonempty_dimension_tuple"] += bool(typed["dimensions"])
                counts["source_period_lexical_form_differs_from_exported_period"] += (
                    typed["period"].get("instant") is not None
                    and typed["period"]["instant"] != old["canonical_dimensions"].get("period"))
                if typed["period"]["kind"] == "instant":
                    exported = old["canonical_dimensions"].get("period", "")
                    source_day = typed["period"]["instant"][:10]
                    if re.match(r"^\d{4}-\d{2}-\d{2}", exported):
                        delta = (date.fromisoformat(exported[:10]) - date.fromisoformat(source_day)).days
                        counts["exported_instant_calendar_date_differs_from_source"] += delta != 0
                        counts["exported_instant_calendar_date_one_day_after_source"] += delta == 1
            except (ValueError, KeyError, TypeError) as exc:
                record["teacher_unknown_reason"] = str(exc)[:200]
                counts["unknown_teacher_tuple"] += 1
            if ident in records:
                raise ValueError("Duplicate historical target")
            records[ident] = record
    if set(records) != set(feedback):
        raise ValueError("Historical full-binding inventory incomplete")
    teachers = DEST / "training_only/past_binding_feedback.json"
    save(teachers, records)
    save(manifest_file, {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "historical_semantic_teacher_preparation_not_model_evaluation",
        "historical_targets": len(records), "historical_source_reports": counts["historical_report_files_read"], "counts": dict(counts),
        "source_and_audit_sha256": hashes, "past_feedback_sha256": sha(feedback_file),
        "historical_reference_view": "full_publisher_reference_view_v2",
        "teacher_sha256": sha(teachers), "public_input_sha256": sha(public_file), "runner_sha256": sha(__file__),
        "future_or_reserved_test_report_content_or_labels_read": False,
        "new_model_inference_or_accuracy_result": False,
        "teacher_semantics_supplied_to_student": False,
        "visible_header_ownership_or_visual_identifiability_certified": False,
        "unavailable_teacher_tuples_preserved_as_unknown": True,
        "human_runtime_confirmations": 0,
    })
    print("Prepared historical full-binding teacher inventory", len(records), dict(counts), flush=True)


if __name__ == "__main__":
    prepare()
