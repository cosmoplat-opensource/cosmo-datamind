"""Additive application workflow and run-output migration — DR-072."""
SCHEMA = (
    'CREATE TABLE robotics_adaptations(adaptation_id TEXT PRIMARY KEY, record TEXT NOT NULL)',
    '''CREATE TABLE robotics_adaptation_requests(scope TEXT NOT NULL,request_id TEXT NOT NULL,
       fingerprint TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(scope,request_id))''',
    '''CREATE TABLE robotics_application_documents(version_id TEXT PRIMARY KEY REFERENCES versions(version_id),
       path TEXT NOT NULL UNIQUE,sha256 TEXT NOT NULL)''',
)


def migrate(conn):
    # Rebuild related run tables together, retaining every existing field and index.
    tables = ('runs', 'run_events', 'run_artifacts', 'build_requests', 'design_executions')
    rows = {t: [dict(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}
    sql = {t: [r[0] for r in conn.execute('SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL '
           'ORDER BY type DESC,rowid', (t,))] for t in tables}
    sql['runs'][0] = sql['runs'][0].replace('AND output_asset_ref IS NULL)',
        "AND (output_asset_ref IS NULL OR operation='adapt_execution'))")
    for t in reversed(tables):
        conn.execute(f'DELETE FROM {t}'); conn.execute(f'DROP TABLE {t}')
    for t in tables:
        for statement in sql[t]: conn.execute(statement)
        for row in rows[t]:
            conn.execute(f'INSERT INTO {t} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
    for statement in SCHEMA: conn.execute(statement)
    if conn.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('应用迁移外键检查失败。')
    conn.execute('PRAGMA user_version=8')
