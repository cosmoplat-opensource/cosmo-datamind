"""Existing real build/storage boundaries remain valid before application integration — DR-071 / IR-025."""
import pytest

from domain_runs import RunStore
from domains.robotics.consumer import load_ontology_bytes
from ontology_assets import AssetStore
from ontology_assets.robotics_design import DesignAssets
from tests.application_contract_support import no_network
from tests.build_pipeline_support import begin, blob_bytes, compute, db_rows
from tests.robotics_design_support import Harness

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_existing_offline_build_is_stored_without_application_selection_or_approval(tmp_path):
    root, assets, runs, _, run = begin(tmp_path)
    product = compute()(root, run['run_id'])
    saved = runs.finish_build(run['run_id'], **product)
    assert saved['status'] == 'completed'
    ref = saved['output_asset_ref']
    assert any(asset['ontology_id'] == ref['ontology_id'] for asset in assets.list_assets())
    version = assets.get_version(**ref)
    assert version['publication_state'] == 'candidate'
    assert assets.read_payload(**ref) == product['ontology']
    assert product['result']['task_readiness'] == 'not_assessed'


def test_confirmed_llm_design_can_be_stored_and_read_despite_failing_ur_application(tmp_path):
    # Real TBox/ABox pipeline + SQLite, deterministic scripted model; never an external LLM.
    harness = Harness(tmp_path / 'model-build')
    state = harness.candidate()
    assert state['stage'] == 'abox' and state['status'] == 'completed'
    ref = state['output_asset_ref']
    assets = AssetStore(harness.root)
    assert any(asset['ontology_id'] == ref['ontology_id'] for asset in assets.list_assets())
    raw = assets.read_payload(**ref)
    with pytest.raises(ValueError):
        load_ontology_bytes(raw, mode='real')
    assert RunStore(harness.root).get_run(state['run_id'])['status'] == 'completed'
    before, files = db_rows(harness.root), blob_bytes(harness.root)
    model_calls = len(harness.runtime.calls)
    reader = DesignAssets(harness.root, read_only=True)
    assert reader.detail(**ref)['tbox'] == state['schema_confirmation']['tbox']
    assert reader.payload(**ref) == raw
    assert reader.export_json(**ref)
    assert db_rows(harness.root) == before and blob_bytes(harness.root) == files
    assert len(harness.runtime.calls) == model_calls
