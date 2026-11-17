"""Migration and all service admission boundaries, in disposable stores — DR-157."""
import json
import sqlite3

import pytest
import server

from ontology_assets.lifecycle import LifecycleService
from ontology_assets.storage import Storage
from tests.unit.test_asset_lifecycle import sample, apply, blobs, metadata, rows
from tests.unit.test_asset_lifecycle_http import qa_http, activate, request

__all__=['sample','qa_http']


def schema8(sample):
    with sqlite3.connect(sample['root']/'domain.sqlite3') as conn:
        for table in ['asset_lifecycle_requests','asset_lifecycle_events','asset_lifecycle','legacy_tombstones','asset_lifecycle_settings']:
            conn.execute('DROP TABLE IF EXISTS '+table)
        conn.execute('PRAGMA user_version=8')


def test_v8_migration_preserves_existing_rows_and_blob_bytes(sample):
    schema8(sample);before=metadata(sample['root']),blobs(sample['root'])
    service=LifecycleService(sample['root'])
    with service.storage.transaction() as conn:assert conn.execute('PRAGMA user_version').fetchone()[0]==9
    for table,records in before[0].items():assert rows(sample['root'],table)==records
    assert blobs(sample['root'])==before[1]


def test_failed_v8_migration_rolls_back_schema_and_existing_data(sample,monkeypatch):
    schema8(sample);before=metadata(sample['root']),blobs(sample['root'])
    from ontology_assets import lifecycle_schema
    original=lifecycle_schema.migrate
    def fail(conn):original(conn);raise sqlite3.OperationalError('simulated migration failure')
    monkeypatch.setattr(lifecycle_schema,'migrate',fail)
    with pytest.raises(ValueError) as exc:LifecycleService(sample['root'])
    assert exc.value.code=='storage_unavailable'
    assert before==(metadata(sample['root']),blobs(sample['root']))
    with sqlite3.connect(sample['root']/'domain.sqlite3') as conn:assert conn.execute('PRAGMA user_version').fetchone()[0]==8


def test_readonly_v8_does_not_migrate_or_rewrite_anything(sample):
    schema8(sample);before=metadata(sample['root']),blobs(sample['root'])
    with pytest.raises(ValueError):LifecycleService(sample['root'],read_only=True)
    assert before==(metadata(sample['root']),blobs(sample['root']))


@pytest.mark.parametrize('kind',['design','adaptation'])
def test_deleted_or_restored_waiting_services_reject_before_model_or_mutation(sample,kind):
    service=LifecycleService(sample['root']);ref=sample['refs'][1];oid=ref['asset']['ontology_id'];version=ref['versions'][0]['version_id']
    if kind=='design':
        from domain_runs.robotics_design_service import DesignService
        target=DesignService(sample['root'])
        doc={'session_id':'design_wait','last_asset_ref':{'ontology_id':oid,'version_id':version},'state':{'revision':0},'status':'needs_description'}
        with target.storage.transaction(write=True) as conn:target._put(conn,doc)
        action=lambda:target.message('design_wait',text='new',request_id='next',expected_revision=0,expected_stage='classes')
    else:
        from domain_runs.robotics_application_service import ApplicationService
        target=ApplicationService(sample['root'])
        doc={'adaptation_id':'adapt_wait','asset_ref':{'ontology_id':oid,'version_id':version},'revision':0,'status':'awaiting_role_confirmation'}
        with target._atomic(write=True) as conn:target._put(conn,doc)
        action=lambda:target.confirm_roles('adapt_wait',bindings=[],expected_revision=0,request_id='next')
    apply(service,oid)
    with pytest.raises(ValueError) as exc:action()
    assert exc.value.code=='ontology_deleted'
    apply(service,oid,'restore','restore')
    with pytest.raises(ValueError) as exc:action()
    assert exc.value.code=='stale_lifecycle'


def test_design_run_with_null_asset_columns_still_blocks_deleting_its_input_asset(sample):
    service=LifecycleService(sample['root']);ref=sample['refs'][1];oid=ref['asset']['ontology_id'];vid=ref['versions'][0]['version_id']
    from domain_runs.robotics_design_service import DesignService
    from domain_runs import RunStore
    design=DesignService(sample['root']);runs=RunStore(sample['root'])
    with design.storage.transaction(write=True) as conn:
        design._put(conn,{'session_id':'design_busy','status':'queued','last_asset_ref':{'ontology_id':oid,'version_id':vid},'state':{'revision':0}})
        inputs={'ontology_id':None,'version_id':None,'operation':'design_tbox','task':{},'mode':'real',
                'session_ref':{'namespace':'robotics_design','id':'design_busy'},'components':{},'design_input':{'revision':0}}
        runs._insert(conn,inputs,None)
    with pytest.raises(ValueError) as exc:apply(service,oid)
    assert exc.value.code=='asset_busy'


def test_corrupt_references_fail_closed_without_deleting_asset(sample):
    service=LifecycleService(sample['root']);oid=sample['refs'][0]['asset']['ontology_id']
    with service.storage.transaction(write=True) as conn:
        conn.execute('INSERT INTO design_sessions VALUES (?,?)',('broken','not-json'))
    with pytest.raises(ValueError) as exc:apply(service,oid)
    assert exc.value.code=='reference_unavailable' and service.get(oid)['state']=='active'


@pytest.mark.parametrize('kind',['source_ids','session_record'])
def test_wrong_reference_shapes_fail_closed(sample,kind):
    service=LifecycleService(sample['root']);oid=sample['refs'][0]['asset']['ontology_id']
    with service.storage.transaction(write=True) as conn:
        if kind=='source_ids':conn.execute("UPDATE versions SET source_asset_ids='{}' WHERE ontology_id=?",(oid,))
        else:conn.execute('INSERT INTO design_sessions VALUES (?,?)',('broken_shape','[]'))
    with pytest.raises(ValueError) as exc:apply(service,oid)
    assert exc.value.code=='reference_unavailable' and service.get(oid)['state']=='active'


def test_explicit_legacy_edit_is_versioned_and_undo_restores_effective_content(qa_http):
    service,oid=activate(qa_http);client=qa_http[0];first=service.resolve_legacy('built_scope')['version_id']
    response=client.post('/api/ont/apply',json={'graph':'built_scope','op':{'op':'set_alias','target':'obj:customer','params':{'aliases':'验收客户'}}})
    assert response.status_code==200,response.json
    second=service.resolve_legacy('built_scope')['version_id'];assert first!=second
    assert server.load_ir_edited('built_scope')['objects'][0]['aliases']==['验收客户']
    assert client.post('/api/ont/undo',json={'graph':'built_scope'}).status_code==200
    assert server.load_ir_edited('built_scope')['objects'][0].get('aliases',[])==[]


@pytest.mark.parametrize('key',['built_scope','demo'])
def test_metric_blueprint_writes_publish_a_new_fixed_legacy_version(qa_http,key):
    raw={**qa_http[3],'metric_layers':{'atomic':[{'name':'lifecycle_metric','status':'candidate'}]}}
    path=qa_http[1]/('demo.json' if key=='demo' else key+'.json');path.write_text(json.dumps(raw))
    service,_=activate(qa_http);before=service.resolve_legacy(key)
    response=qa_http[0].post('/api/metric/status',json={'graph':key,'metric':'lifecycle_metric','status':'deprecated'})
    assert response.status_code==200,response.json
    assert server.load_ir_edited(key)['metric_layers']['atomic'][0]['status']=='deprecated'
    after=service.resolve_legacy(key)
    assert after['ontology_id']==before['ontology_id'] and after['version_id']!=before['version_id']


def test_failed_legacy_save_restores_raw_edit_file_and_binding(qa_http,monkeypatch):
    service,_=activate(qa_http);key='built_scope';before=service.resolve_legacy(key);path=qa_http[1]/'edits_built_scope.json'
    def fail(*args,**kw):raise OSError('simulated immutable save failure')
    monkeypatch.setattr(Storage,'write_bytes',fail)
    response=qa_http[0].post('/api/ont/apply',json={'graph':key,'op':{'op':'set_alias','target':'obj:customer','params':{'aliases':'should roll back'}}})
    assert response.status_code>=400
    assert not path.exists() and service.resolve_legacy(key)==before


def test_restored_alias_survives_restart_without_raw_file_reimport(qa_http):
    service,oid=activate(qa_http);old=service.resolve_legacy('built_scope')['version_id']
    request(qa_http[0],oid);(qa_http[1]/'built_scope.json').write_text('{"objects":[]}')
    assert request(qa_http[0],oid,'restore').status_code==200
    server.init_asset_lifecycle()
    assert service.resolve_legacy('built_scope')['version_id']==old
    assert server.load_ir_edited('built_scope')['objects'][0]['table']=='customers'


def test_disabling_rollout_flag_does_not_remove_persisted_delete_guards(qa_http,monkeypatch):
    service,oid=activate(qa_http);request(qa_http[0],oid)
    monkeypatch.setenv('DATAMIND_LIFECYCLE_ENABLED','0')
    response=qa_http[0].post('/api/build/delete',json={'key':'built_scope'})
    assert response.status_code==400 and response.json['error']['code']=='confirmation_required'
    assert service.get(oid)['state']=='deleted'


def test_complete_reference_digest_covers_versions_beyond_display_limit(qa_http):
    service,oid=activate(qa_http)
    from ontology_assets import AssetStore
    assets=AssetStore(qa_http[1]/'domain_assets');raw=b'{"objects":[]}'
    for _ in range(102):assets.add_version(oid,raw,payload_kind='datamind_ir')
    preview=qa_http[0].post(f'/api/ontology-assets/{oid}/deletion-preview',json={}).json
    assert len(preview['impact']['version_ids'])==100 and preview['impact_totals']['version_ids']==103
    assets.add_version(oid,raw,payload_kind='datamind_ir')
    response=qa_http[0].post(f'/api/ontology-assets/{oid}/delete',json={'expected_revision':0,
        'impact_sha256':preview['impact_sha256'],'request_id':'tail-changed','confirmed':True})
    assert response.status_code==409 and response.json['error']['code']=='impact_changed'
