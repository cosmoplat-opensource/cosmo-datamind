"""Additive governance migration; no historic row/file rewrites — DR-059 / DR-064."""
SCHEMA = (
    '''CREATE TABLE governance_heads (
        ontology_id TEXT PRIMARY KEY REFERENCES assets(ontology_id),
        version_id TEXT NOT NULL REFERENCES versions(version_id), revision INTEGER NOT NULL CHECK(revision>=1))''',
    '''CREATE TABLE governance_events (
        event_id TEXT PRIMARY KEY, ontology_id TEXT NOT NULL REFERENCES assets(ontology_id),
        version_id TEXT REFERENCES versions(version_id), record TEXT NOT NULL)''',
    '''CREATE TABLE governance_requests (
        ontology_id TEXT NOT NULL REFERENCES assets(ontology_id), request_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(ontology_id,request_id))''',
    '''CREATE TABLE governance_reviews (
        review_id TEXT PRIMARY KEY, ontology_id TEXT NOT NULL REFERENCES assets(ontology_id),
        version_id TEXT NOT NULL REFERENCES versions(version_id), record TEXT NOT NULL)''',
    '''CREATE TABLE version_artifacts (
        artifact_id TEXT PRIMARY KEY, ontology_id TEXT NOT NULL REFERENCES assets(ontology_id),
        version_id TEXT NOT NULL REFERENCES versions(version_id), kind TEXT NOT NULL,
        record TEXT NOT NULL, UNIQUE(version_id,kind))''',
    '''CREATE TABLE evidence_revocations (
        revocation_id TEXT PRIMARY KEY, ontology_id TEXT NOT NULL REFERENCES assets(ontology_id),
        version_id TEXT NOT NULL REFERENCES versions(version_id), evidence_iri TEXT NOT NULL,
        record TEXT NOT NULL)''',
    'CREATE INDEX governance_events_by_asset ON governance_events(ontology_id)',
    'CREATE INDEX evidence_revocations_by_asset ON evidence_revocations(ontology_id)',
)


def migrate(conn):
    for statement in SCHEMA: conn.execute(statement)
    if conn.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('治理迁移外键校验失败。')
    conn.execute('PRAGMA user_version=5')
