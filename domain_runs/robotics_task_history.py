"""Read-only application history over existing sessions and immutable runs — DR-069."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from ontology_assets import AssetStore
from .store import RunStore, _decode
from .robotics_task_service import TaskError, _tree


def _title(state, run=None):
    if run:
        saved = state.get('generation_titles', {}).get(run['run_id'])
        if saved:
            return saved
        task = run['task']
        short = lambda value: str(value or '未指定').rsplit('#', 1)[-1]
        return f"将{short(task.get('part'))}放到{short(task.get('slot') or task.get('station'))}"
    return state.get('task_description') or next(
        (m['content'] for m in state.get('messages', []) if m['role'] == 'user'), '未开始的任务')


def _legacy_titles(conn, sessions):
    """Recover original descriptions from durable responses without rewriting old data."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='robotics_task_requests'").fetchone():
        return
    for row in conn.execute("SELECT response FROM robotics_task_requests WHERE json_extract(response, '$.run_id') IS NOT NULL ORDER BY rowid"):
        saved = json.loads(row[0])
        state = sessions.get(saved.get('session_id'))
        if state is not None:
            title = saved.get('task_description') or next(
                (m['content'] for m in saved.get('messages', []) if m['role'] == 'user'), None)
            if title:
                state.setdefault('generation_titles', {}).setdefault(saved['run_id'], title)


class TaskHistory:
    def __init__(self, root):
        self.root = Path(root)

    def list(self, *, limit=30, offset=0, q=''):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise TaskError('invalid_request', '分页参数不正确。')
        if not isinstance(q, str) or len(q) > 200:
            raise TaskError('invalid_request', '搜索内容过长。')
        empty = {'items': [], 'total': 0, 'limit': limit, 'offset': offset}
        if not (self.root / 'domain.sqlite3').is_file():
            return empty
        assets = AssetStore(self.root, read_only=True)
        with assets.storage.transaction() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='robotics_task_sessions'").fetchone():
                return empty
            sessions = {row['session_id']: json.loads(row['record']) for row in conn.execute(
                'SELECT * FROM robotics_task_sessions ORDER BY rowid')}
            _legacy_titles(conn, sessions)
            names = {row['ontology_id']: row['name'] for row in conn.execute("SELECT ontology_id,name FROM assets WHERE domain='robotics'")}
            rows, generated_sessions = [], set()
            for row in conn.execute("SELECT * FROM runs WHERE operation='generate_bt' AND status='completed' ORDER BY rowid"):
                run = _decode(row)
                ref = run['session_ref'] or {}
                state = sessions.get(ref.get('id'))
                if not state or ref.get('namespace') != 'robotics_task' or (run['result'] or {}).get('status') != 'generated':
                    continue
                if run['ontology_id'] != state['ontology_id'] or run['ontology_id'] not in names:
                    continue
                generated_sessions.add(state['session_id'])
                rows.append({'kind': 'generation', 'session_id': state['session_id'], 'run_id': run['run_id'],
                    'ontology_id': run['ontology_id'], 'version_id': run['version_id'], 'mode': run['mode'],
                    'title': _title(state, run), 'status': 'generated', 'updated_at': run['created_at']})
            for state in sessions.values():
                if state['ontology_id'] not in names or (state['status'] == 'generated' and state['session_id'] in generated_sessions):
                    continue
                rows.append({'kind': 'task', 'session_id': state['session_id'], 'run_id': None,
                    'ontology_id': state['ontology_id'], 'version_id': state['version_id'], 'mode': state['mode'],
                    'title': _title(state), 'status': state['status'], 'updated_at': state.get('updated_at')})
            for row in rows:
                row['asset_name'] = names[row['ontology_id']]
            query = q.strip().casefold()
            rows = [row for row in rows if not query or query in (row['title'] + ' ' + row['asset_name']).casefold()]
            # Stable ordering for timestamp-free legacy records; no invented migration dates.
            rows.reverse()
            rows.sort(key=lambda row: row['updated_at'] or '', reverse=True)
            selected = rows[offset:offset + limit]
            for row in selected:
                row['version_number'] = assets._version_number(conn, row['ontology_id'], row['version_id'])
            return {'items': selected, 'total': len(rows), 'limit': limit, 'offset': offset}

    def _read(self, session_id, run_id):
        if not (self.root / 'domain.sqlite3').is_file():
            raise TaskError('not_found', '未找到这份生成记录。')
        assets, runs = AssetStore(self.root, read_only=True), RunStore(self.root, read_only=True)
        try:
            with assets.storage.transaction() as conn:
                row = conn.execute('SELECT record FROM robotics_task_sessions WHERE session_id=?', (session_id,)).fetchone()
                if row is None:
                    raise KeyError(session_id)
                state = json.loads(row[0])
                _legacy_titles(conn, {session_id: state})
                run = runs.get_run(run_id)
                if (run['operation'] != 'generate_bt' or run['ontology_id'] != state['ontology_id']
                        or run['session_ref'] != {'namespace': 'robotics_task', 'id': session_id}
                        or run['status'] != 'completed' or (run['result'] or {}).get('status') != 'generated'):
                    raise KeyError(run_id)
                name = conn.execute("SELECT name FROM assets WHERE ontology_id=? AND domain='robotics'", (run['ontology_id'],)).fetchone()
                if not name:
                    raise KeyError(run_id)
                runs._verify_snapshot(conn, run)
                manifest = runs.manifest(run_id)
                artifacts = [a for a in manifest['artifacts'] if a['kind'] == 'candidate']
                if len(artifacts) != 1:
                    raise ValueError('missing candidate')
                raw = runs.read_artifact(run_id, artifacts[0]['artifact_id'])
                body = {key: run[key] for key in ('run_id', 'ontology_id', 'version_id', 'mode', 'task', 'result', 'created_at')}
                body.update(session_id=session_id, asset_name=name[0], title=_title(state, run),
                    version_number=assets._version_number(conn, run['ontology_id'], run['version_id']),
                    read_only=True, tree=_tree(ET.fromstring(raw)))
                return body, raw
        except KeyError:
            raise TaskError('not_found', '未找到这份任务的生成结果。') from None
        except (ValueError, OSError, ET.ParseError):
            raise TaskError('artifact_integrity', '历史结果或其本体快照校验失败，无法查看或下载。') from None

    def get(self, session_id, run_id):
        return self._read(session_id, run_id)[0]

    def candidate(self, session_id, run_id):
        return self._read(session_id, run_id)[1]
