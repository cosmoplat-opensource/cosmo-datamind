"""SQLite transactions and exclusive immutable byte files — DR-059 / DR-064."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile

from srv_context import confine

SCHEMA_VERSION = 5
SCHEMA = (
    """CREATE TABLE assets (
        ontology_id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE sources (
        source_asset_id TEXT PRIMARY KEY, role TEXT NOT NULL, media_type TEXT NOT NULL,
        sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL, path TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE versions (
        version_id TEXT PRIMARY KEY, ontology_id TEXT NOT NULL REFERENCES assets(ontology_id),
        parent_version_id TEXT REFERENCES versions(version_id), payload_kind TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL, path TEXT NOT NULL UNIQUE,
        source_asset_ids TEXT NOT NULL, metadata TEXT NOT NULL,
        publication_state TEXT NOT NULL, publication_mode TEXT, validation TEXT,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX versions_by_asset ON versions(ontology_id)",
    """CREATE TABLE legacy_assets (
        legacy_key TEXT PRIMARY KEY, ontology_id TEXT NOT NULL UNIQUE REFERENCES assets(ontology_id),
        version_id TEXT NOT NULL REFERENCES versions(version_id), fingerprint TEXT NOT NULL)""",
)

# DR-064: additive v1 -> v2 migration in the same metadata transaction.
RUN_SCHEMA = (
    """CREATE TABLE runs (
        run_id TEXT PRIMARY KEY, ontology_id TEXT NOT NULL REFERENCES assets(ontology_id),
        version_id TEXT NOT NULL REFERENCES versions(version_id), operation TEXT NOT NULL,
        task TEXT NOT NULL, mode TEXT NOT NULL, session_ref TEXT NOT NULL,
        components TEXT NOT NULL, ontology_snapshot TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','interrupted')),
        result TEXT, retry_of TEXT REFERENCES runs(run_id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX runs_by_session ON runs(session_ref)",
    """CREATE TABLE run_events (
        run_id TEXT NOT NULL REFERENCES runs(run_id), seq INTEGER NOT NULL CHECK(seq > 0),
        kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(run_id,seq))""",
    """CREATE TABLE run_artifacts (
        artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
        name TEXT NOT NULL, kind TEXT NOT NULL, media_type TEXT NOT NULL,
        sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL, path TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL, UNIQUE(run_id,name))""",
)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def _migrate_build_runs(conn):
    """DR-064: rebuild the three related tables in one transaction, retaining raw JSON.

    Foreign keys remain enabled. Save children first, remove them before the parent,
    and restore retry references in original insertion order. No artifact is rewritten.
    """
    tables = ("runs", "run_events", "run_artifacts")
    saved = {name: [dict(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY rowid")]
             for name in tables}
    for name in reversed(tables):
        # Delete the entire self-referencing run set before DROP (retry_of).
        conn.execute(f"DELETE FROM {name}")
        conn.execute(f"DROP TABLE {name}")
    statement = RUN_SCHEMA[0].replace("ontology_id TEXT NOT NULL", "ontology_id TEXT")
    statement = statement.replace("version_id TEXT NOT NULL", "version_id TEXT")
    statement = statement.replace("ontology_snapshot TEXT NOT NULL", "ontology_snapshot TEXT")
    statement = statement.removesuffix(")") + ", build_input TEXT, output_asset_ref TEXT, " + """
        CHECK ((build_input IS NULL AND ontology_id IS NOT NULL AND version_id IS NOT NULL
                AND ontology_snapshot IS NOT NULL AND output_asset_ref IS NULL)
            OR (build_input IS NOT NULL AND operation='build_ontology' AND mode='real'
                AND ontology_id IS NULL AND version_id IS NULL AND ontology_snapshot IS NULL)))"""
    for sql in (statement, *RUN_SCHEMA[1:]):
        conn.execute(sql)
    for name in tables:
        for row in saved[name]:
            fields = ",".join(row)
            placeholders = ",".join("?" for _ in row)
            conn.execute(f"INSERT INTO {name} ({fields}) VALUES ({placeholders})", tuple(row.values()))
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("数据库迁移后的外键校验失败。")
    conn.execute("PRAGMA user_version=3")


class Storage:
    def __init__(self, root, *, read_only=False):
        root = Path(root)
        if not root.is_absolute():
            raise ValueError("存储根目录必须是显式绝对路径。")
        self.root = root.resolve()
        self.read_only = read_only
        if read_only:
            if not Path(confine(self.root, "domain.sqlite3")).is_file():
                raise ValueError("只读检查需要已有的资产数据库。")
            with self.transaction() as conn:
                if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise ValueError("只读检查不支持此 schema，且不会执行迁移。")
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with self.transaction(write=True) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError("未识别的已有数据库，拒绝初始化覆盖。")
                for statement in SCHEMA:
                    conn.execute(statement)
                version = 1
            if version == 1:
                for statement in RUN_SCHEMA:
                    conn.execute(statement)
                version = 2
            if version == 2:
                _migrate_build_runs(conn)
                version = 3
            if version == 3:
                # DR-064: admission identity and run enqueue share one transaction.
                conn.execute("""CREATE TABLE build_requests (
                    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id))""")
                conn.execute("CREATE INDEX queued_builds ON runs(operation,status)")
                conn.execute("PRAGMA user_version=4")
                version = 4
            if version == 4:
                from .governance_schema import migrate
                migrate(conn)
            elif version != SCHEMA_VERSION:
                raise ValueError(f"不支持的资产数据库 schema 版本：{version}")

    @contextmanager
    def transaction(self, *, write=False):
        path = confine(self.root, "domain.sqlite3")
        if self.read_only and write:
            raise ValueError("只读存储不能执行写入。")
        database = Path(path).as_uri() + "?mode=ro" if self.read_only else path
        conn = sqlite3.connect(database, uri=self.read_only, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def write_bytes(self, relative, content):
        """Publish a complete file without replacement; DB visibility comes afterwards."""
        if self.read_only:
            raise ValueError("只读存储不能写入文件。")
        target = Path(confine(self.root, relative))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".asset-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        expected = digest(content)
        self.read_bytes(relative, expected)
        return expected

    def read_bytes(self, relative, expected):
        content = Path(confine(self.root, relative)).read_bytes()
        if digest(content) != expected:
            raise ValueError("载荷文件摘要与登记记录不一致。")
        return content
