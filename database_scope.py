"""Resolve registered SQLite sources and ontology provenance without web state."""
from pathlib import Path


def resolve(source, default_db, upload_db, connections):
    if not isinstance(source, str) or not source or len(source) > 80:
        raise ValueError('数据源标识无效')
    if source == 'demo':
        path, name = default_db, '示例主库'
    elif source == 'uploads':
        path, name = upload_db, '上传数据库'
    else:
        conn = next((c for c in connections if c.get('id') == source), None)
        if not conn or conn.get('kind') != 'sqlite':
            raise ValueError('当前页面需选择已登记的 SQLite 数据源；外部库可在连接页查询或只读抽样')
        path, name = conn.get('path'), conn.get('name') or source
    if not path or not Path(path).is_file():
        raise ValueError('数据源文件不存在或不可读，请检查数据连接')
    return {'id': source, 'name': name, 'kind': 'sqlite', 'path': str(Path(path).resolve())}


def graph_path(ir, default_db, upload_db, connections):
    source = ((ir or {}).get('build_manifest') or {}).get('source') or {}
    try:
        return resolve(source.get('id') or 'demo', default_db, upload_db, connections)['path']
    except ValueError:
        return None


def same_path(a, b):
    return bool(a and b and Path(a).resolve() == Path(b).resolve())
