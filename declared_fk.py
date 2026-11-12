"""Shared SQLite declared-FK evidence for LLM and offline builds (DR-093)."""
from __future__ import annotations

import dao_core
from data_catalog import qi


def collect(con, table, cols_of, primary_keys, record_error=None):
    """Group PRAGMA rows by FK id and check actual constraints/rows, including NULL semantics."""
    def report(op, table, column, exc):
        if record_error:
            record_error(op, table, column, exc)

    groups = {}
    try:
        for row in con.execute(f"PRAGMA foreign_key_list({qi(table)})"):
            groups.setdefault(row[0], []).append(row)
    except Exception as exc:
        report("foreign_key_list", table, "", exc)
        return []
    if not groups:
        return []
    orphans, schema_validated = {}, True
    try:
        # Streaming counts avoid retaining arbitrarily many violation rows.
        for row in con.execute(f"PRAGMA foreign_key_check({qi(table)})"):
            orphans[row[3]] = orphans.get(row[3], 0) + 1
    except Exception as exc:
        report("foreign_key_check", table, "", exc)
        schema_validated = False
    links = []
    table_names = {name.casefold(): name for name in cols_of}
    for fk_id, rows in groups.items():
        rows.sort(key=lambda row: row[1])
        parent = table_names.get(rows[0][2].casefold(), rows[0][2])
        child_columns = [row[3] for row in rows]
        parent_columns = [row[4] for row in rows]
        if all(c is None for c in parent_columns):
            parent_columns = primary_keys.get(parent, [])
        ck, pk = ",".join(child_columns), ",".join(c or "" for c in parent_columns)
        valid_keys = (dao_core.key_columns(ck) and dao_core.key_columns(pk)
                      and len(child_columns) == len(parent_columns)
                      and parent in cols_of)
        count = orphans.get(fk_id, 0)
        # 零孤儿行只有在确有引用值时才构成证据:空表或外键列全为 NULL 时,
        # foreign_key_check 必然返回空,此时的「零孤儿」不能作为 verified 的依据。
        referencing = None
        if valid_keys:
            try:
                cond = " AND ".join(f"{qi(c)} IS NOT NULL" for c in child_columns)
                referencing = con.execute(f"SELECT COUNT(*) FROM {qi(table)} WHERE {cond}").fetchone()[0]
            except Exception as exc:
                report("referencing_rows", table, ck, exc)
        verified = bool(schema_validated and valid_keys and not count and referencing)
        status = "verified" if verified else "candidate"
        decision = ("schema_declared_foreign_key" if verified else
                    "declared_fk_no_referencing_rows" if referencing == 0 else "declared_fk_requires_review")
        evidence = {"child_key": ck, "parent_key": pk, "source": "declared_fk", "declared": True,
                    "schema_validated": schema_validated, "orphan_count": count if schema_validated else None,
                    "referencing_rows": referencing,
                    "direction": "self" if table == parent else "child_to_parent",
                    "decision": decision}
        link = {"source_concept": table, "target_concept": parent, "verb": "关联",
                "status": status, "evidence_status": status,
                "semantic": "not_reviewed", "semantic_status": "not_reviewed",
                "overlap": None, "note": f"声明FK ({ck})→{parent}.({pk})" +
                ("" if verified else
                 "；子表无非空引用值,外键未被任何数据见证,送审" if referencing == 0 else
                 "；约束或引用数据未通过核验，送审"), "evidence": evidence,
                "founded_relation": "", "grounding_iri": "", "grounding_status": "unmapped",
                "grounding_reason": "声明引用仅证实数据连接；业务含义仍待审", "temporal": ""}
        if table == parent:
            link["self_ref"] = True
            evidence["self_ref"] = True
        links.append(link)
    return links


def apply(con, ir, record_error=None):
    """Attach grouped declared checks to bound endpoints, preserving human decisions.

    Extra verified keys retain their own evidence; failed alternatives never
    inherit the primary relationship's verified state.
    """
    from data_catalog import schema
    tables = schema(con)
    primary_keys = {t: [r[1] for r in sorted(con.execute(f'PRAGMA table_info({qi(t)})'), key=lambda r: r[5]) if r[5]]
                    for t in tables}
    names = {t.casefold(): t for t in tables}
    t2o = {}
    for o in ir.get('objects') or []:
        t = names.get(str(o.get('table') or '').casefold())
        if t and o.get('name'):
            t2o.setdefault(t, o['name'])
    groups = {}
    for t in t2o:
        for link in collect(con, t, tables, primary_keys, record_error):
            parent = link['target_concept']
            if parent not in t2o:
                continue
            link['source_concept'], link['target_concept'] = t2o[t], t2o[parent]
            groups.setdefault((t2o[t], t2o[parent]), []).append(link)
    relations = ir.setdefault('relations', [])
    existing = {(r.get('source_concept'), r.get('target_concept')): r for r in relations}
    for pair, checks in groups.items():
        checks.sort(key=lambda r: r['status'] != 'verified')
        primary, rest = checks[0], checks[1:]
        valid = [r['evidence'] for r in rest if r['status'] == 'verified']
        pending = [r['evidence'] for r in rest if r['status'] != 'verified']
        if valid:
            primary['evidence']['role_keys'] = valid
        if pending:
            primary['evidence']['role_candidates'] = pending
        cur = existing.get(pair)
        if cur and cur.get('status') in ('asserted', 'rejected'):
            # A schema reference checks connectivity, not a reviewer's business judgment.
            cur['declared_checks'] = [r['evidence'] for r in checks]
        elif cur:
            for k in ('status', 'evidence_status', 'evidence', 'note', 'overlap'):
                cur[k] = primary[k]
        else:
            relations.append(primary)
    return len(groups)

