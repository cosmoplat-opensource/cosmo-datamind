"""Sealed-company frozen test, step 0: pre-registered selection of the test companies.

The 60 companies with role reserved_independent_test in source_qualification/registry_metadata_v1/design.json were
sealed on 2026-10-16 and never opened.  On 2026-10-26 the user approved opening about ten of them for ONE frozen
test of the final systems (v34 and its no-LLM variant), to be run once and reported as found.

Rule (fixed before any filing is downloaded or rendered):
  * eligible = sealed companies whose frozen local filings index (ext/xbrl_scope_source_index, fetched 2026-10-16)
    lists an ESEF filing for three consecutive fiscal year ends 2023, 2024 and 2025 with the same month-day;
  * the eligible LEIs are sorted and shuffled once with random.Random(SEED); the first N are the test companies;
  * a company is replaced by the next one in the order only for a technical failure recorded in selection.json
    (package over the byte cap, package checksum mismatch, no page with >= 6 visible numeric facts, fewer than
    10 teacher cells in 2023); never because of its result;
  * 2023 filings are the company's own teachers, 2024 and 2025 filings are the test, exactly as for heldout_v1.
Writes <root>/selection.json; nothing else is touched.
"""

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from prepare import save, sha

EXP = Path(__file__).resolve().parents[1]
INDEX = EXP / "ext/xbrl_scope_source_index"
DESIGN = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/source_qualification/registry_metadata_v1/design.json"
ROOT = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1"
SEED, N = 20261027, 10
YEARS = ("2023", "2024", "2025")


def index_filings(leis):
    out = {lei: {} for lei in leis}
    for f in sorted(INDEX.glob("gb_page_*.json")):
        for item in json.loads(f.read_text()).get("data", []):
            a = item.get("attributes", {})
            lei = a.get("fxo_id", "").split("-")[0]
            if lei in out and a.get("package_url") and a.get("period_end"):
                prev = out[lei].get(a["period_end"])
                if prev is None or a["processed"] > prev["processed"]:
                    out[lei][a["period_end"]] = {"fxo_id": a["fxo_id"], "package_url": a["package_url"], "json_url": a["json_url"], "report_url": a["report_url"],
                                                 "sha256": a["sha256"], "processed": a["processed"], "error_count": a["error_count"]}
    return out


def main():
    if (ROOT / "selection.json").exists():
        raise RuntimeError("sealed selection already frozen")
    design = json.loads(DESIGN.read_text())
    sealed = {x["lei"]: x["provided_name"] for x in design["selected_identifiers"] if x["tentative_role"] == "reserved_independent_test"}
    filings = index_filings(sealed)
    eligible = {}
    for lei, by_end in filings.items():
        for end in sorted(by_end):
            if end[:4] == YEARS[0] and all((y + end[4:]) in by_end for y in YEARS):
                eligible[lei] = tuple(y + end[4:] for y in YEARS); break
    order = sorted(eligible); random.Random(SEED).shuffle(order)
    chosen = order[:N]
    record = {"frozen_utc": datetime.now(timezone.utc).isoformat(), "approved_by_user": "2026-10-26", "seed": SEED, "n": N,
              "rule": __doc__.strip(), "sealed_total": len(sealed), "eligible_total": len(eligible), "order": order,
              "companies": chosen, "names": {lei: sealed[lei] for lei in order},
              "filings": {lei: list(eligible[lei]) for lei in chosen},
              "index_records": {lei: {end: filings[lei][end] for end in eligible[lei]} for lei in chosen},
              "replacements": [], "design_sha256": sha(DESIGN), "selector_sha256": sha(Path(__file__))}
    ROOT.mkdir(parents=True, exist_ok=True)
    save(ROOT / "selection.json", record)
    print("sealed selection frozen:", len(eligible), "eligible of", len(sealed), "->", chosen, flush=True)


if __name__ == "__main__":
    main()
