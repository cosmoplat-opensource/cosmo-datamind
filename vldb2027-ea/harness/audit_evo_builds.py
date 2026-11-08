#!/usr/bin/env python3
"""Audit the EvoOntology builder sessions on the opaque variants for reads outside the permitted inputs.

The builder is an agent with file-system access. Permitted: the variant database, the construction workload file, its
own workspace, and the EvoOntology plugin. Flagged: any tool call whose input mentions the opaque mapping, rewritten
questions or dictionaries, any BIRD split directory (construction/validation/test with gold SQL), the original gold SQL
field, or a path under runs/ other than the builder's own workspace.
usage: python3 audit_evo_builds.py [build-log glob]   (default runs/v2/build_logs/evo-w__opaque*/*.jsonl)
Writes runs/v4/evo_build_audit.json and exits 1 if any session is flagged.
"""
import glob
import json
import re
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
BAD = re.compile(r"variants/opaque(_merged)?/(mapping|questions|dictionaries)|mapping\.json|"
                 r"minidev(_rev)?/(test|validation|construction)/|SQL_original|/runs/(?!v2/evo/w_opaque)")


def audit(path):
    hits, calls = [], 0
    for line in open(path, errors="replace"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") != "assistant":
            continue
        for c in (ev.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                calls += 1
                s = json.dumps(c.get("input"), ensure_ascii=False)
                m = BAD.search(s)
                if m:
                    hits.append({"tool": c.get("name"), "match": m.group(0), "excerpt": s[max(0, m.start() - 120):m.end() + 60]})
    return calls, hits


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else str(EXP / "runs/v2/build_logs/evo-w__opaque*/*.jsonl")
    out, flagged = {}, 0
    for f in sorted(glob.glob(pattern)):
        if ".attempt" in f:
            continue
        calls, hits = audit(f)
        key = str(Path(f).relative_to(EXP))
        out[key] = {"tool_calls": calls, "flagged": hits}
        flagged += bool(hits)
        print(f"{key}: {calls} tool calls, {len(hits)} flagged")
    (EXP / "runs/v4").mkdir(parents=True, exist_ok=True)
    (EXP / "runs/v4/evo_build_audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{len(out)} sessions, {flagged} flagged")
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
