"""v34: presentation- and calculation-linkbase features for field candidates.

The candidate inventory already reads the declaration members of every report's own ESEF package
(schema, label and definition linkbases; never an instance member).  This module reads two further
declaration linkbases of the same packages, the presentation linkbase (_pre.xml) and the calculation
linkbase (_cal.xml), from the frozen local copies under visual_bench_v0/assets.  For each field
candidate of a target it derives indicator features: whether the concept is presented in any network,
in a primary-statement network, in a network whose statement type matches the words of the target page,
whether it carries a negated or total preferred label, whether it is a summation parent or child, and
how well the labels of its presentation neighbours (previous / next sibling, parent) match the OCR row
labels directly above / below the target.  No fact, reference or teacher file is read here.
"""

import json
import re
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import native_structural_prior_v29 as p29
from native_v30_bank import DEST as BANK
from prepare import save, sha

ASSETS = Path(__file__).resolve().parents[1] / "visual_bench_v0/assets"
DEST = BANK / "linkbase_v34"
XL = "{http://www.w3.org/1999/xlink}"; LB = "{http://www.xbrl.org/2003/linkbase}"
FEATURES = ["pre_statement", "pre_any", "pre_negated", "pre_total", "cal_parent", "cal_child", "cal_any", "pre_role_match", "pre_role_other", "pre_neighbour", "pre_parent_below"]
TYPES = {"sfp": r"financial ?position|balance ?sheet", "pl": r"profit ?or ?loss|income ?statement|profit ?and ?loss",
         "oci": r"comprehensive ?income", "cf": r"cash ?flow", "sce": r"changes ?in ?equity"}
ROLE_CODES = {"sfp": r"210000", "pl": r"310000|320000", "oci": r"410000|420000", "cf": r"510000|520000", "sce": r"610000"}


def package_for(report):
    lei, year = report.split("_")
    hits = sorted(d for d in ASSETS.iterdir() if d.name.startswith(lei + "_" + year))
    if len(hits) != 1:
        raise ValueError("ambiguous or missing asset folder for " + report)
    z = hits[0] / "package.zip"
    return z if z.exists() else None


def key_of(href):
    frag = href.split("#")[-1]
    prefix, name = frag.split("_", 1)
    return ("ifrs" if "ifrs" in href or prefix.startswith("ifrs") else "ext", name)


def cand_key(meta):
    return ("ifrs" if "ifrs" in (meta.get("namespace") or "") else "ext", meta.get("name", ""))


def role_types(role):
    r = role.lower().replace("_", "")
    return {k for k in TYPES if re.search(TYPES[k].replace(" ?", ""), r) or re.search(ROLE_CODES[k], r)}


def page_types(words):
    text = " ".join(w["text"] for w in words).lower()
    return {k for k, pat in TYPES.items() if re.search(pat, text)}


def parse_linkbases(zpath):
    out = {}

    def ent(k):
        return out.setdefault(k, {"pre_any": 0, "pre_statement": 0, "negated": 0, "total": 0, "cal_parent": 0, "cal_child": 0, "pos": []})
    with zipfile.ZipFile(zpath) as z:
        names = sorted(n for n in z.namelist() if n.lower().endswith(("_pre.xml", "_cal.xml")) and "META-INF" not in n)
        for n in names:
            root = ET.fromstring(z.read(n))
            for link in root:
                if link.tag not in (LB + "presentationLink", LB + "calculationLink"):
                    continue
                role = link.get(XL + "role", ""); is_statement = not re.search(r"notes|mandatory|110000|general", role, re.I)
                locs = {l.get(XL + "label"): key_of(l.get(XL + "href")) for l in link if l.tag == LB + "loc"}
                for arc in link:
                    if arc.tag == LB + "presentationArc":
                        f, t = locs.get(arc.get(XL + "from")), locs.get(arc.get(XL + "to"))
                        if t is None:
                            continue
                        e = ent(t); e["pre_any"] = 1
                        if is_statement:
                            e["pre_statement"] = 1; e["pos"].append((role, f, float(arc.get("order", "0") or 0)))
                        pl = arc.get("preferredLabel", "")
                        if "negated" in pl:
                            e["negated"] = 1
                        if "total" in pl.lower():
                            e["total"] = 1
                    elif arc.tag == LB + "calculationArc":
                        f, t = locs.get(arc.get(XL + "from")), locs.get(arc.get(XL + "to"))
                        if f:
                            ent(f)["cal_parent"] = 1
                        if t:
                            ent(t)["cal_child"] = 1
    return out


def row_text(words, yc, h, x_max):
    sel = [w for w in words if abs((w["box"][1] + w["box"][3]) / 2 - yc) < 0.55 * h and w["box"][0] < x_max and not re.fullmatch(r"[\d,().%\-–]+", w["text"])]
    return " ".join(w["text"] for w in sorted(sel, key=lambda w: w["box"][0]))


def jacc(a, b):
    ta, tb = set(p29.toks(a)), set(p29.toks(b))
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def siblings(lk, key):
    out = []
    for role, parent, order in lk.get(key, {}).get("pos", []):
        sibs = sorted(((o, k) for k, e in lk.items() for (r, par, o) in e["pos"] if r == role and par == parent), key=lambda x: (x[0], x[1]))
        keys = [k for _, k in sibs]
        i = keys.index(key) if key in keys else -1
        out.append((keys[i - 1] if i > 0 else None, keys[i + 1] if 0 <= i < len(keys) - 1 else None, parent))
    return out


def label_of(key):
    return p29.camel(key[1])


def load_links(reports):
    links, manifest = {}, {}
    for r in sorted(reports):
        z = package_for(r)
        if z is None:
            links[r] = {}; manifest[r] = None
        else:
            links[r] = parse_linkbases(z); manifest[r] = {"package": str(z), "sha256": sha(z), "statement_concepts": sum(e["pre_statement"] for e in links[r].values())}
    return links, manifest


def candidate_features(task, item, record, words, lk):
    """List (one dict per field candidate group) of the v34 linkbase features."""
    groups = item["components"]["field"]
    tb = task["target_box"]; h = max(tb[3] - tb[1], 0.004)
    ptypes = page_types(words); above = row_text(words, tb[1] - 0.9 * h, h, tb[0]); below = row_text(words, tb[3] + 0.9 * h, h, tb[0])
    rows = []
    for g in groups:
        row = {k: 0.0 for k in FEATURES}
        rows.append(row)
        if g["value"] is None or not g["source_record_indices"] or g["source_record_indices"][0] >= len(record["field"]):
            continue
        meta = record["field"][g["source_record_indices"][0]]["metadata"]
        key = cand_key(meta); e = lk.get(key)
        if not e:
            continue
        row.update({"pre_statement": float(e["pre_statement"]), "pre_any": float(e["pre_any"]), "pre_negated": float(e["negated"]), "pre_total": float(e["total"]),
                    "cal_parent": float(e["cal_parent"]), "cal_child": float(e["cal_child"]), "cal_any": float(e["cal_parent"] or e["cal_child"])})
        rts = set().union(*(role_types(r) for r, _, _ in e["pos"])) if e["pos"] else set()
        row["pre_role_match"] = float(bool(rts & ptypes)); row["pre_role_other"] = float(bool(rts) and not (rts & ptypes))
        best = pb = 0.0
        for pred, succ, parent in siblings(lk, key):
            if pred:
                best = max(best, jacc(label_of(pred), above))
            if succ:
                best = max(best, jacc(label_of(succ), below))
            if parent:
                pb = max(pb, jacc(label_of(parent), below))
        row["pre_neighbour"] = best; row["pre_parent_below"] = pb
    return rows


def build(tasks, inventory, records, pages, anchors):
    links, manifest = load_links({t["report"] for t in tasks})
    feats = {t["task_id"]: candidate_features(t, item, rec, pages[anchors[t["task_id"]]["page_hash"]]["words"], links[t["report"]]) for t, item, rec in zip(tasks, inventory, records, strict=True)}
    return feats, manifest


def main():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("v34 linkbase features already frozen")
    tasks = json.loads((BANK / "public/tasks.json").read_text()); inventory = json.loads((BANK / "public/witnesses.json").read_text())
    records = json.loads((BANK / "public/records.json").read_text())
    pages = json.loads((p29.ALIGN / "localization/public_pages.json").read_text()); anchors = json.loads((p29.ALIGN / "localization/public_anchors.json").read_text())
    feats, manifest = build(tasks, inventory, records, pages, anchors)
    DEST.mkdir(parents=True, exist_ok=True)
    save(DEST / "linkbase_features.json", feats)
    save(DEST / "manifest.json", {"bank": "common_candidate_v30", "features": FEATURES, "packages": manifest, "reports_without_local_package": sorted(r for r, m in manifest.items() if m is None),
                                  "members_read": "_pre.xml and _cal.xml only (declaration linkbases); no instance member", "mean_candidates_in_statement_networks": sum(sum(r["pre_statement"] for r in v) for v in feats.values()) / len(feats),
                                  "linkbase_features_sha256": sha(DEST / "linkbase_features.json"), "source_sha256": sha(Path(__file__))})
    print("v34 linkbase features frozen for", len(feats), "targets;", len([m for m in manifest.values() if m]), "packages read", flush=True)


if __name__ == "__main__":
    main()
