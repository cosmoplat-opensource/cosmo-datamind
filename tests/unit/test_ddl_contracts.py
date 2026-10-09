"""v2:DDL 声明外键作为连接契约(code_contracts.ddl_contracts)。"""
import sqlite3


def _mk(tmp_path):
    db = tmp_path / "t.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE Patient(ID INTEGER PRIMARY KEY, SEX TEXT);
    CREATE TABLE Laboratory(ID INTEGER, Date TEXT, GOT INTEGER, PRIMARY KEY(ID, Date),
        FOREIGN KEY (ID) REFERENCES Patient(ID));
    CREATE TABLE Player(id INTEGER PRIMARY KEY, player_api_id INTEGER UNIQUE);
    CREATE TABLE Match(id INTEGER PRIMARY KEY, home_player_1 INTEGER, home_player_2 INTEGER,
        FOREIGN KEY (home_player_1) REFERENCES Player(player_api_id),
        FOREIGN KEY (home_player_2) REFERENCES Player(player_api_id));
    CREATE TABLE Orders(oid INTEGER PRIMARY KEY, cust INTEGER REFERENCES Patient);
    """)
    con.commit(); con.close()
    return db


def test_ddl_contracts_single_composite_and_unnamed_parent(tmp_path):
    import code_contracts
    db = _mk(tmp_path)
    con = sqlite3.connect(db)
    tab_cols = {t: [(r[1], r[2]) for r in con.execute(f'PRAGMA table_info("{t}")')]
                for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    out = code_contracts.ddl_contracts(db, tab_cols)
    pairs = {(x["a"][0], x["a"][1], x["b"][0], x["b"][1]) for x in out}
    assert ("Laboratory", "ID", "Patient", "ID") in pairs
    assert ("Match", "home_player_1", "Player", "player_api_id") in pairs
    assert ("Match", "home_player_2", "Player", "player_api_id") in pairs
    assert ("Orders", "cust", "Patient", "ID") in pairs      # REFERENCES Patient 未写列名 → 父表主键
    assert all(x["file"] == "DDL" and x["declared"] for x in out)
    hit = code_contracts.hit(out, "Match", "home_player_2", "Player", "player_api_id")
    assert hit and hit["declared"]
    assert code_contracts.hit(out, "Match", "id", "Player", "id") is None


def test_ddl_contracts_absent_on_nofk_schema(tmp_path):
    import code_contracts
    db = tmp_path / "n.sqlite"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE A(id INTEGER PRIMARY KEY); CREATE TABLE B(id INTEGER PRIMARY KEY, a_id INTEGER);")
    con.commit(); con.close()
    assert code_contracts.ddl_contracts(db, {"A": [("id", "INTEGER")], "B": [("id", "INTEGER"), ("a_id", "INTEGER")]}) == []
