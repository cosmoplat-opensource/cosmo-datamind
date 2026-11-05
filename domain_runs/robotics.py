"""Generate from a pinned published asset and commit durable outputs — DR-062 / DR-064 / DR-065."""
from pathlib import Path
import tempfile

from domains.robotics import compiler, generation
from ontology_assets import AssetStore
from ontology_assets.payloads import json_text
from ontology_assets.storage import digest
from srv_context import _RDF_LOCK

from .store import RunStore, _run, _expect


def execution_components(version, validation):
    """Record the actual compiler file and consumed rules, never model credentials."""
    return {"profile_id": version["metadata"].get("profile_id"),
            "compiler_version": "sha256:" + digest(Path(compiler.__file__).read_bytes()),
            "rule_digest": validation["validation_shapes_sha256"],
            "prompt_version": None, "model": {"invoked": False}}


def generate_from_version(root, *, ontology_id, version_id, task, mode, session_ref):
    assets = AssetStore(root)
    context = assets.load_robotics(ontology_id, version_id, mode=mode)
    version = assets.get_version(ontology_id, version_id)
    from domains.robotics.evidence import policy
    if policy(context['graph']):
        from ontology_assets.governance import GovernanceService
        context['evidence_revocations'] = GovernanceService(root, read_only=True).revocations(ontology_id)
    runs = RunStore(root)
    run = runs.create_run(ontology_id=ontology_id, version_id=version_id, operation="generate_bt",
        task=task, mode=mode, session_ref=session_ref, components=execution_components(version, context["validation"]))
    runs.start(run["run_id"])
    artifacts = []
    try:
        with tempfile.TemporaryDirectory(prefix=".robotics-", dir=runs.storage.root) as raw:
            directory = Path(raw)
            # The kernel revalidates RDF/SHACL; share the existing lock, with no LLM work inside.
            with _RDF_LOCK:
                result = generation.generate_task(context, run["task"], mode=mode, output_dir=directory)
            if result["status"] == "generated":
                candidate = Path(result["candidate_path"]).resolve()
                if not result["candidate_written"] or not candidate.is_relative_to(directory):
                    raise ValueError("生成结果未指向本次受控目录中的候选。")
                artifacts.append({"name": candidate.name, "kind": "candidate", "media_type": "application/xml",
                                  "content": candidate.read_bytes()})
            elif result.get("candidate_written") or result.get("candidate_path"):
                raise ValueError("负向生成结果不能附带候选。")
            result = {key: value for key, value in result.items() if key != "candidate_path"}
    except Exception as exc:
        result = {"status": "error", "mode": mode, "candidate_written": False,
                  "execution_readiness": "not_verified", "parameter_sources": [],
                  "issues": [{"code": "generation_error", "message": f"{type(exc).__name__}: {exc}"[:1600]}]}
        artifacts = []
    artifacts.append({"name": "validation.json", "kind": "validation", "media_type": "application/json",
                      "content": json_text(result).encode("utf-8")})
    # A failed durable commit must propagate; never convert it to a pretend completed report.
    if policy(context['graph']):
        return _finish_governed(runs,run['run_id'],context,result,artifacts)
    return runs.finish(run["run_id"], result=result, artifacts=artifacts)


def _finish_governed(runs, run_id, context, result, artifacts):
    """DR-061/064: serialize current evidence admission with durable completion.

    A revocation that commits during computation must block the candidate. The
    ordinary RunStore completion contract and old manifest layout stay intact.
    """
    import json
    from domains.robotics import grounding, evidence
    from . import contracts
    with _RDF_LOCK, runs.storage.transaction(write=True) as conn:
        run = _run(conn,run_id)
        _expect(run,'running')
        runs._verify_snapshot(conn,run)
        revocations = [json.loads(row[0]) for row in conn.execute(
            'SELECT record FROM evidence_revocations WHERE ontology_id=? ORDER BY rowid',(run['ontology_id'],))]
        if result['status'] == 'generated':
            resolved = grounding.resolve_request(context['graph'],
                {key:run['task'][key] for key in ('part','station','carrier','slot')},
                mode=run['mode'],revocations=revocations)
            if resolved['issues']:
                result = {**result,'status':'needs_information','candidate_written':False,
                          'issues':resolved['issues'],'grounding':resolved}
                artifacts = [item for item in artifacts if item['kind'] != 'candidate']
        result = {**result,'evidence_check':{'policy':evidence.POLICY,'checked_at':evidence.utc_now(),
                   'revocation_ids':[item['revocation_id'] for item in revocations]}}
        artifacts = [{**item,'content':json_text(result).encode('utf-8')} if item['kind']=='validation' else item
                     for item in artifacts]
        outcome, checked = contracts.completion(result,artifacts)
        return runs._complete(conn,run,outcome,checked)
