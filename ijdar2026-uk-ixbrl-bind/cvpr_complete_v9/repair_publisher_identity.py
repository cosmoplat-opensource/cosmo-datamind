"""Private annotation recovery when native numeric tags have no DOM ID.

Use full declared concept/entity/period/unit/dimension keys, never a match to
the displayed number or a model answer. Preserve the old reference audits and
frozen rendered pixels. Unsupported or conflicting contexts stay unresolved.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import re

from lxml import etree

from canonicalization_prepare import ROOT
from prepare import save, sha

XI = "{http://www.xbrl.org/2003/instance}"
XD = "{http://xbrl.org/2006/xbrldi}"
IX = "{http://www.xbrl.org/2013/inlineXBRL}"


def qname(value, namespaces):
    prefix, separator, local = value.partition(":")
    if not separator or prefix not in namespaces:
        raise ValueError("Undeclared qualified name")
    return "{" + namespaces[prefix] + "}" + local


def date_time(value, end=False):
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        parsed = datetime.fromisoformat(value)
        if end:
            parsed += timedelta(days=1)
        return parsed.isoformat()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()


def period(node):
    instant = node.find(XI + "instant")
    if instant is not None:
        return date_time(instant.text, end=True)
    start, end = node.find(XI + "startDate"), node.find(XI + "endDate")
    if start is None or end is None:
        raise ValueError("Unsupported forever or missing period")
    return date_time(start.text) + "/" + date_time(end.text, end=True)


def unit(node):
    ns = node.nsmap
    divide = node.find(XI + "divide")
    if divide is None:
        return "*".join(sorted(qname(m.text, ns) for m in node.findall(XI + "measure")))
    numerator = divide.find(XI + "unitNumerator")
    denominator = divide.find(XI + "unitDenominator")
    if numerator is None or denominator is None:
        raise ValueError("Incomplete divided unit")
    return (
        "*".join(sorted(qname(m.text, ns) for m in numerator.findall(XI + "measure")))
        + "/"
        + "*".join(
            sorted(qname(m.text, ns) for m in denominator.findall(XI + "measure"))
        )
    )


def context(node):
    entity = node.find(XI + "entity/" + XI + "identifier")
    if entity is None:
        raise ValueError("Missing reporting entity")
    result = {
        "entity": "{" + entity.get("scheme") + "}" + entity.text.strip(),
        "period": period(node.find(XI + "period")),
    }
    if list(node.iter(XD + "typedMember")):
        raise ValueError("Typed dimensions require separate qualified conversion")
    for member in node.iter(XD + "explicitMember"):
        axis = qname(member.get("dimension"), member.nsmap)
        if axis in result:
            raise ValueError("Duplicate context dimension")
        result[axis] = qname(member.text, member.nsmap)
    return result


def exported_key(dimensions, namespaces):
    result = {}
    for key, value in dimensions.items():
        if key in ("concept", "entity"):
            result[key] = qname(value, namespaces)
        elif key == "unit":
            result[key] = "/".join(
                "*".join(sorted(qname(v, namespaces) for v in part.split("*")))
                for part in value.split("/")
            )
        elif key == "period":
            result[key] = "/".join(date_time(v) for v in value.split("/"))
        elif ":" in key:
            result[qname(key, namespaces)] = qname(value, namespaces)
        else:
            # Numeric targets do not use language or custom core dimensions.
            raise ValueError("Unsupported exported dimension")
    return json.dumps(result, sort_keys=True)


def native_key(node, contexts, units):
    result = dict(contexts[node.get("contextRef")])
    result["concept"] = qname(node.get("name"), node.nsmap)
    result["unit"] = units[node.get("unitRef")]
    return json.dumps(result, sort_keys=True)


def same_reference(facts):
    # Duplicate semantic keys can point to a single shared reference value.
    # Conflicting published values remain unresolved. Displayed/model values
    # are not consulted to choose one of the exported facts.
    if not facts:
        return False
    try:
        return len({Decimal(str(f["value"])) for f in facts}) == 1
    except Exception:
        return False


def root_for(node):
    for ancestor in node.iterancestors():
        if any(
            re.fullmatch(r"(pf(?:_\d+)?|page|pageView)", token, re.I)
            for token in ancestor.get("class", "").split()
        ):
            return ancestor
    return None


def main():
    source_manifest = ROOT / "render_source_manifest.json"
    sources = json.loads(source_manifest.read_text())["reports"]
    original_render_file = ROOT / "native_renders_v3/render_manifest_dpr2.json"
    renders = json.loads(original_render_file.read_text())
    source_map = {(r["entity"], r["period_end"]): r for r in sources}
    dest = ROOT / "publisher_identity_v2"
    totals = Counter()
    reports = []
    for render in renders:
        key = render["entity"], render["period_end"]
        source = source_map[key]
        original_audit = ROOT / "scoring_only" / (key[0] + "_" + key[1] + ".json")
        audit = json.loads(original_audit.read_text())
        if all(row.get("published_fact_found") for row in audit["rows"]):
            reports.append(
                {"entity": key[0], "period_end": key[1], "original_audit_reused": True}
            )
            continue
        if (
            sha(source["xhtml"]) != audit["xhtml_sha256"]
            or sha(source["facts_scoring_only"]) != audit["facts_sha256"]
        ):
            raise RuntimeError("Original private source annotation changed")
        parser = etree.XMLParser(
            resolve_entities=False, no_network=True, huge_tree=True
        )
        tree = etree.parse(source["xhtml"], parser)
        published = json.loads(Path(source["facts_scoring_only"]).read_text())
        ns = published["documentInfo"]["namespaces"]
        by_key = defaultdict(list)
        for ident, fact in published["facts"].items():
            if "unit" not in fact["dimensions"]:
                continue
            try:
                by_key[exported_key(fact["dimensions"], ns)].append((ident, fact))
            except ValueError:
                totals["unsupported_exported_fact_dimensions"] += 1
        contexts, units = {}, {}
        for node in tree.iter(XI + "context"):
            try:
                contexts[node.get("id")] = context(node)
            except (ValueError, TypeError):
                totals["unsupported_native_contexts"] += 1
        for node in tree.iter(XI + "unit"):
            try:
                units[node.get("id")] = unit(node)
            except (ValueError, TypeError):
                totals["unsupported_native_units"] += 1
        nodes = list(tree.iter(IX + "nonFraction"))
        if len(nodes) != len(audit["rows"]):
            raise RuntimeError("Original numeric-tag audit order changed")
        by_root = defaultdict(list)
        root_order = []
        for index, (node, row) in enumerate(zip(nodes, audit["rows"])):
            if (
                node.get("name") != row["concept"]
                or node.get("contextRef") != row["context_ref"]
            ):
                raise RuntimeError("Private numeric DOM order mismatch")
            if not row["published_fact_found"]:
                try:
                    matches = by_key.get(native_key(node, contexts, units), [])
                except (ValueError, KeyError, TypeError):
                    matches = []
                if same_reference([f for _, f in matches]):
                    fact = matches[0][1]
                    row.update(
                        published_fact_found=True,
                        canonical_dimensions=fact["dimensions"],
                        canonical_value=fact["value"],
                        declared_decimals=fact.get("decimals"),
                        matching_publisher_fact_ids=[ident for ident, _ in matches],
                        matched_by_full_declared_semantic_key=True,
                        canonical_value_used_to_match_display=False,
                    )
                    totals["recovered_numeric_annotations"] += 1
                else:
                    totals["unresolved_missing_or_conflicting_semantic_key"] += 1
            row["private_dom_ordinal"] = index
            if not row["region_id"]:
                row["region_id"] = "private-dom-ordinal-" + str(index)
            root = root_for(node)
            if root is not None:
                if root not in by_root:
                    root_order.append(root)
                by_root[root].append((node, row))
        for index, page in enumerate(render["selected_pages"]):
            if sha(page["image_path"]) != page["image_sha256"]:
                raise RuntimeError("Frozen native pixels changed")
            if render.get("images_reused_from_version") == 2:
                roots = [r for r in root_order if r.get("id") == page["page_id"]]
                if len(roots) != 1:
                    raise RuntimeError("Cannot resolve original native page ID")
                root = roots[0]
            else:
                if page["page_id"] != "native-page-" + str(index):
                    raise RuntimeError("Unsupported geometry fallback page mapping")
                root = root_order[index]
            region_rows = by_root[root]
            if len(region_rows) != len(page["scoring_only_region_boxes"]):
                raise RuntimeError(
                    "Private DOM and frozen numeric geometry counts differ"
                )
            for region, (node, row) in zip(
                page["scoring_only_region_boxes"], region_rows
            ):
                if (
                    re.sub(r"\s+", " ", region["text"]).strip()
                    != re.sub(r"\s+", " ", "".join(node.itertext())).strip()
                ):
                    raise RuntimeError(
                        "Private DOM and frozen numeric geometry text differ"
                    )
                region["private_reference_key"] = row["region_id"]
        save(dest / "scoring_only" / original_audit.name, audit)
        reports.append(
            {
                "entity": key[0],
                "period_end": key[1],
                "original_audit_reused": False,
                "matched_rows": sum(r["published_fact_found"] for r in audit["rows"]),
                "numeric_tags": len(nodes),
                "original_audit_sha256": sha(original_audit),
            }
        )
    save(dest / "render_scoring_only.json", renders)
    save(
        dest / "manifest.json",
        {
            "counts": dict(totals),
            "reports": reports,
            "original_publisher_values_not_rewritten": True,
            "original_pixels_not_modified": True,
            "old_reference_audits_preserved": True,
            "full_semantic_keys_not_numeric_similarity_used": True,
            "publisher_numeric_values_not_method_inputs": True,
            "scope_ownership_not_certified": True,
            "human_confirmations": 0,
            "runner_sha256": sha(__file__),
            "original_render_manifest_sha256": sha(original_render_file),
            "xbrl_date_semantics_source": "https://specifications.xbrl.org/registries/functions-registry-1.0/80127%20xfi.period-end/80127%20xfi.period-end%20function.html",
        },
    )
    print("Private no-ID publisher annotation recovery:", dict(totals), flush=True)


if __name__ == "__main__":
    main()
