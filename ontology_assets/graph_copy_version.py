"""Save the replayed RDF as a detached candidate of its fixed source — DR-191."""
from .lifecycle import require_active
from .payloads import json_text
from .robotics_application import is_application
from .storage import digest
from .store import _required


def save_copy(assets, parent, operations, content, role_map):
    oid, vid = parent['ontology_id'], parent['version_id']
    fingerprint = digest(json_text(operations).encode())
    with assets.storage.transaction(write=True) as conn:
        require_active(conn, oid)
        existing = conn.execute("""SELECT version_id, path, payload_sha256 FROM versions WHERE ontology_id=? AND parent_version_id=?
            AND json_extract(metadata, '$.graph_edit.parent')=?
            AND json_extract(metadata, '$.graph_edit.operations_sha256')=?""", (oid, vid, vid, fingerprint)).fetchone()
        if existing:
            assets.storage.read_bytes(existing['path'], existing['payload_sha256'])
            return {'ontology_id': oid, 'version_id': existing['version_id']}
        edit = {'parent': vid, 'operations_sha256': fingerprint}
        if role_map:
            edit['role_map'] = role_map
        metadata = {key: parent['metadata'][key] for key in ('mode', 'evidence_policy') if key in parent['metadata']}
        metadata['graph_edit'] = edit
        sources = parent['source_asset_ids']
        if is_application(parent):
            # Original XML may be archived. Its audit references belong to the parent bundle.
            attachments = []
            for sid in sources:
                source = _required(conn, 'SELECT role FROM sources WHERE source_asset_id=?', (sid,))
                if source['role'] not in ('behavior_tree', 'node_models'):
                    attachments.append(sid)
            sources = attachments
        child = assets._add_version(conn, oid, content.encode('utf-8'), payload_kind='robotics_rdf',
                                    parent_version_id=vid, source_asset_ids=sources, metadata=metadata,
                                    inherit_application=False)
        return {'ontology_id': oid, 'version_id': child['version_id']}
