"""Additive lifecycle metadata; immutable assets and files stay intact — DR-157."""
SCHEMA = (
    '''CREATE TABLE asset_lifecycle(ontology_id TEXT PRIMARY KEY REFERENCES assets(ontology_id),
       state TEXT NOT NULL CHECK(state IN ('active','deleted')), revision INTEGER NOT NULL CHECK(revision>=0),
       protected INTEGER NOT NULL CHECK(protected IN (0,1)))''',
    '''CREATE TABLE asset_lifecycle_events(operation_id TEXT PRIMARY KEY,
       ontology_id TEXT NOT NULL REFERENCES assets(ontology_id),record TEXT NOT NULL)''',
    '''CREATE TABLE asset_lifecycle_requests(ontology_id TEXT NOT NULL REFERENCES assets(ontology_id),
       request_id TEXT NOT NULL,fingerprint TEXT NOT NULL,operation TEXT NOT NULL,
       PRIMARY KEY(ontology_id,request_id))''',
    'CREATE TABLE legacy_tombstones(legacy_key TEXT PRIMARY KEY,record TEXT NOT NULL)',
    'CREATE TABLE asset_lifecycle_settings(name TEXT PRIMARY KEY,value TEXT NOT NULL)',
)


def migrate(conn):
    for sql in SCHEMA: conn.execute(sql)
    conn.execute('PRAGMA user_version=9')
