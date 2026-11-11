"""Explicit schema-5 to schema-6 design migration, retaining historic bytes — DR-070."""

SCHEMA = (
    '''CREATE TABLE design_sessions (
        session_id TEXT PRIMARY KEY, record TEXT NOT NULL)''',
    '''CREATE TABLE design_revisions (
        session_id TEXT NOT NULL REFERENCES design_sessions(session_id),
        revision INTEGER NOT NULL CHECK(revision>=0), record TEXT NOT NULL,
        PRIMARY KEY(session_id,revision))''',
    '''CREATE TABLE design_requests (
        session_id TEXT NOT NULL REFERENCES design_sessions(session_id), request_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(session_id,request_id))''',
    '''CREATE TABLE design_admissions (
        request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL,
        session_id TEXT NOT NULL UNIQUE REFERENCES design_sessions(session_id))''',
    '''CREATE TABLE design_executions (
        run_id TEXT PRIMARY KEY REFERENCES runs(run_id))''',
)


def migrate(conn):
    # All existing SQL, indexes and row values survive; only runs gains a new branch.
    tables = ('runs', 'run_events', 'run_artifacts', 'build_requests')
    saved = {name: [dict(row) for row in conn.execute(f'SELECT * FROM {name} ORDER BY rowid')] for name in tables}
    statements = {name: [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL ORDER BY type DESC,rowid", (name,))]
        for name in tables}
    run_sql = statements['runs'][0]
    prefix, separator, _ = run_sql.partition('CHECK ((build_input')
    if not separator:
        raise ValueError('旧运行表约束不匹配，拒绝迁移。')
    statements['runs'][0] = prefix + '''design_input TEXT,
        CHECK ((design_input IS NULL AND build_input IS NULL
                AND operation NOT IN ('design_tbox','extract_abox')
                AND ontology_id IS NOT NULL AND version_id IS NOT NULL
                AND ontology_snapshot IS NOT NULL AND output_asset_ref IS NULL)
            OR (design_input IS NULL AND build_input IS NOT NULL AND operation='build_ontology' AND mode='real'
                AND ontology_id IS NULL AND version_id IS NULL AND ontology_snapshot IS NULL)
            OR (design_input IS NOT NULL AND build_input IS NULL AND operation IN ('design_tbox','extract_abox')
                AND mode='real' AND ontology_id IS NULL AND version_id IS NULL AND ontology_snapshot IS NULL)))'''
    for name in reversed(tables):
        conn.execute(f'DELETE FROM {name}')
        conn.execute(f'DROP TABLE {name}')
    for name in tables:
        for sql in statements[name]:
            conn.execute(sql)
        for row in saved[name]:
            fields, placeholders = ','.join(row), ','.join('?' for _ in row)
            conn.execute(f'INSERT INTO {name} ({fields}) VALUES ({placeholders})', tuple(row.values()))
    for sql in SCHEMA:
        conn.execute(sql)
    if conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('设计会话迁移外键检查失败。')
    conn.execute('PRAGMA user_version=6')
