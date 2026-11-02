"""Reproduce archived robotics boundaries in temporary copies — DR-065 / IR-016 T01.

This is an audit tool for the explicitly supplied archive, not the integrated runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


FILES = (
    "ontology/ontology_pipeline.py", "ontology/skill_runtime.py",
    "ontology/xingweishu.xml", "ontology/xml_models.xml", "ontology/ur_skill_shapes.ttl",
    "ontology/ur_skill.owl", "ontology/test_ontology.py", "ontology/test_skill_runtime.py",
    "ontology/test_ontology_only.py", "agent/ontology_backend.py", "agent/skill_runtime.py",
    "agent/trusted_agent.py", "agent/llm_client.py", "agent/test_skill_runtime.py",
    "agent/test_owl_contract.py", "agent/input/ur_skill.owl", "agent/demo/ur_skill_demo.owl",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archive, output = args.archive.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(archive):
        parser.error("Use a new report path outside the source archive.")
    hashes = {name: digest(archive / name) for name in FILES}
    report = {
        "iteration": "IR-016", "source_sha256": hashes,
        "python": sys.version, "dependencies": {
            name: importlib.metadata.version(name) for name in ("rdflib", "pyshacl")
        }, "cases": [],
    }
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BT_AGENT_", "DATAMIND_", "OPENAI_", "ANTHROPIC_"))
           and k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="robotics-baseline-") as raw:
        temp = Path(raw)
        for name in FILES:
            target = temp / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive / name, target)

        def run(name, folder, code, expected_exit=0, expected_text=None):
            # Audit source is trusted; forbid network connections for every probe.
            guard = "import socket\ndef offline(*a, **k): raise AssertionError('network forbidden')\nsocket.socket.connect=offline\nsocket.create_connection=offline\n"
            result = subprocess.run([sys.executable, "-B", "-c", guard + code],
                                    cwd=temp / folder, env=env, capture_output=True,
                                    text=True, timeout=120, check=False)
            matched = result.returncode == expected_exit and (
                expected_text is None or expected_text in result.stdout + result.stderr)
            row = {"name": name, "expected_exit": expected_exit, "actual_exit": result.returncode,
                   "matched_baseline": matched, "stdout": result.stdout, "stderr": result.stderr}
            report["cases"].append(row)
            print(f"{name}: exit={result.returncode}, baseline_match={matched}", flush=True)
            return result

        for folder, test in (("ontology", "test_ontology.py"), ("ontology", "test_skill_runtime.py"),
                             ("agent", "test_skill_runtime.py"), ("agent", "test_owl_contract.py")):
            run(f"{folder}/{test}", folder, f"import runpy; runpy.run_path({test!r}, run_name='__main__')")
        run("cross_directory_import_collision", "ontology",
            "import runpy; runpy.run_path('test_ontology_only.py', run_name='__main__')",
            expected_exit=1, expected_text="has no attribute 'CONDITION_SPECS'")
        run("fresh_build", "ontology", """
import ontology_pipeline as p
g=p.build_graph()
assert p.graph_validation(g)[0]
assert not list(g.objects(p.ONTOLOGY,p.S.artifactContract))
g.serialize('../fresh.owl',format='xml')
print('fresh build: local SHACL passed; artifactContract absent')
""")
        run("consumer_contract_matrix", "agent", """
import ontology_backend as p
for path in ('../fresh.owl','../ontology/ur_skill.owl'):
    try: p.load_ontology(path)
    except ValueError as exc:
        assert 'artifactContract' in str(exc), str(exc)
        print(path, 'REJECT', str(exc))
    else: raise AssertionError('Expected contract rejection')
ctx=p.load_ontology('input/ur_skill.owl')
print('agent/input/ur_skill.owl ACCEPT')
demo=p.load_ontology('demo/ur_skill_demo.owl',mode='demo')
print('agent/demo/ur_skill_demo.owl ACCEPT demo')
""")
        run("draft_vs_strict_policy", "ontology", """
import inspect, ontology_pipeline as p
assert 'allow_unresolved=True' in inspect.getsource(p.compile_candidate)
print('authoring compile_candidate explicitly permits unresolved values')
""")
        run("strict_incomplete_task", "agent", """
import tempfile
from pathlib import Path
from trusted_agent import AgentService
class Client:
    configured=True; missing=[]; model='offline-fixture'; live=False
    def parse(self,*args):
        return dict(operation='pick_place',part='零部件A',station='工位B',carrier='Carrier_B_01',slot='Slot_B_01',unsupported_requests=[])
with tempfile.TemporaryDirectory() as raw:
    service=AgentService(client=Client(),output_dir=Path(raw),ontology_paths={'real':Path('input/ur_skill.owl').resolve()})
    result=service.chat('把零部件A放到工位B的Carrier_B_01的Slot_B_01',mode='real')
    assert result['status']=='needs_information',result
    assert not result['candidate_written']
    print(result['status'], sorted({i['code'] for i in result['issues']}))
""")
    report["source_unchanged"] = hashes == {name: digest(archive / name) for name in FILES}
    report["matched_baseline"] = report["source_unchanged"] and all(
        case["matched_baseline"] for case in report["cases"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return 0 if report["matched_baseline"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
