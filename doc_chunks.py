#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长文证据的分块与检索:把整份上传文档切成可追溯的片段,按本轮构建需要挑进提示词。

背景:构建取证原先对每份上传文档只读前 3500 字、文档总块再截到 9000 字。一份
十几万字的数据库设计文档,模型只能看到开头约 2%——排在后面的表的中文含义、枚举
与关系说明全部丢失,而这些恰恰是提议对象与关系最需要的证据。

两种读法合在一起:
  1. 锚定(分段读):本轮数据源里的每张表,都去文档里找到描述它的那一段——
     保证「表有了、说明却没进来」的情况尽量不发生;
  2. 检索(RAG):剩余预算按构建诉求、验收问题做 BM25 相关性排序补齐。
入选片段一律带出处(文件 · 标题路径 · 块号),模型的 evidence 字段可以回指。

只用标准库:检索是词法的(中文字二元组 + 标识符词),不依赖向量模型,结果可复现。
"""
from __future__ import annotations

import math
import re
from collections import Counter

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BOLD_TITLE = re.compile(r"^\*\*([^*]{1,80})\*\*\s*$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}")
_IDENT = re.compile(r"[a-z0-9][a-z0-9_]*")
_CJK = re.compile(r"[一-鿿]+")


def tokenize(text):
    """中文取字二元组(单字词也保留),英文/标识符取整词并拆开下划线。"""
    text = (text or "").lower()
    toks = []
    for word in _IDENT.findall(text):
        toks.append(word)
        parts = [p for p in word.split("_") if len(p) >= 2]
        if len(parts) > 1:
            toks.extend(parts)
    for seq in _CJK.findall(text):
        if len(seq) == 1:
            toks.append(seq)
        toks.extend(seq[i:i + 2] for i in range(len(seq) - 1))
    return toks


def _compact(text):
    """压缩 Markdown 表格的排版噪声:去分隔行、收拢多余空白。不改变任何内容字。"""
    out = []
    for ln in text.splitlines():
        if _TABLE_SEP.match(ln.strip()):
            continue
        out.append(re.sub(r"[ \t]{2,}", " ", ln).rstrip())
    return "\n".join(x for x in out if x.strip())


def chunk_text(text, source, max_chars=900, overlap=120):
    """按标题切块;超长块再按表格行/段落切,表格续块重复表头。返回片段列表。"""
    lines = (text or "").splitlines()
    blocks, path, cur, cur_start = [], [], [], 0

    def flush(end_line):
        body = "\n".join(cur).strip()
        if body:
            blocks.append({"path": " > ".join(t for _, t in path), "text": body, "line": cur_start + 1})

    for i, ln in enumerate(lines):
        heading, bold = _HEADING.match(ln), _BOLD_TITLE.match(ln)
        # 独占一行、形如「名称 —— 说明」或「名称(说明)」的加粗行是子标题(设计文档里常见的子表写法);
        # 「**索引：** `x`」这类后面还跟内容的标签行不算
        if bold and not re.search(r"[—（(]", bold.group(1)):
            bold = None
        if heading or bold:
            flush(i); cur, cur_start = [ln], i
            if heading:
                level, title = len(heading.group(1)), heading.group(2).strip()
            else:
                level, title = ((path[-1][0] + 1) if path else 1), bold.group(1).strip()
            path = [(lv, t) for lv, t in path if lv < level] + [(level, title)]
            continue
        cur.append(ln)
    flush(len(lines))

    chunks = []
    for b in blocks:
        body = _compact(b["text"])
        for piece in _split_block(body, max_chars, overlap):
            chunks.append({"id": f"{source}#{len(chunks)}", "source": source, "index": len(chunks),
                           "path": b["path"], "line": b["line"], "text": piece})
    return chunks


def _split_block(body, max_chars, overlap):
    if len(body) <= max_chars:
        return [body]
    lines = body.splitlines()
    head = lines[0] if lines and not lines[0].startswith("|") else ""
    table_hdr = next((ln for ln in lines if ln.startswith("|")), "")
    pieces, buf = [], []
    for ln in lines:
        if buf and len("\n".join(buf)) + len(ln) + 1 > max_chars:
            pieces.append("\n".join(buf))
            tail = "\n".join(buf)[-overlap:] if overlap and not ln.startswith("|") else ""
            buf = [x for x in (head, table_hdr if ln.startswith("|") and ln != table_hdr else "", tail) if x]
        buf.append(ln[:max_chars])
    if buf:
        pieces.append("\n".join(buf))
    return pieces


class BM25:
    def __init__(self, docs_tokens, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(t) for t in docs_tokens]
        self.len = [len(t) for t in docs_tokens]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        df = Counter()
        for tf in self.tf:
            df.update(tf.keys())
        n = len(self.tf)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def score(self, i, query_tokens):
        tf, dl, s = self.tf[i], self.len[i], 0.0
        for w in set(query_tokens):
            f = tf.get(w)
            if not f:
                continue
            s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
        return s


def select(chunks, query="", anchors=(), budget=16000, anchor_share=0.7, max_chunk=900, min_anchor_chars=240):
    """两段式选取:先锚定(每个锚点一段),再按查询检索补满预算。

    anchors:通常是本轮数据源的表名。锚点的命中以「标识符整词出现在标题路径或正文」为准,
    标题路径命中优先——设计文档里描述某张表的正是以表名为标题的那一段。
    返回 (选中的片段列表[按出处与块号排序], 统计)。
    """
    toks = [tokenize(c["path"] + "\n" + c["text"]) for c in chunks]
    bm = BM25(toks) if chunks else None
    chosen, used = {}, 0
    anchors = [a for a in dict.fromkeys(a for a in anchors if a)]
    anchor_budget = int(budget * anchor_share)
    per_anchor = max(min_anchor_chars, min(max_chunk, anchor_budget // max(1, len(anchors)))) if anchors else 0
    covered, unbudgeted = [], []
    for a in anchors:
        if used >= anchor_budget:
            unbudgeted.append(a)                       # 文档里可能有,只是锚定预算已用完
            continue
        al = a.lower()
        variants = {al, al.split("__", 1)[-1]}        # 允许「库前缀__表名」对上文档里的裸表名
        best, best_key = None, None
        for i, c in enumerate(chunks):
            in_path = any(re.search(r"(?<![a-z0-9_])" + re.escape(v) + r"(?![a-z0-9_])", c["path"].lower()) for v in variants)
            in_text = in_path or any(re.search(r"(?<![a-z0-9_])" + re.escape(v) + r"(?![a-z0-9_])", c["text"].lower()) for v in variants)
            if not in_text:
                continue
            key = (in_path, bm.score(i, tokenize(" ".join(variants))), -i)
            if best_key is None or key > best_key:
                best, best_key = i, key
        if best is None:
            continue
        covered.append(a)
        if best not in chosen:
            text = chunks[best]["text"][:per_anchor]
            chosen[best] = text; used += len(text)
    qt = tokenize(query)
    if qt and bm:
        ranked = sorted(((bm.score(i, qt), i) for i in range(len(chunks)) if i not in chosen), reverse=True)
        for s, i in ranked:
            if s <= 0 or used >= budget:
                break
            text = chunks[i]["text"][:max_chunk]
            if used + len(text) > budget:
                text = text[:max(0, budget - used)]
                if len(text) < 120:
                    break
            chosen[i] = text; used += len(text)
    picked = [dict(chunks[i], text=t) for i, t in sorted(chosen.items())]
    stats = {"chunks": len(chunks), "selected": len(picked), "chars": used, "budget": budget,
             "anchors": len(anchors), "anchors_covered": len(covered),
             "anchors_unbudgeted": len(unbudgeted),
             "anchors_missing": [a for a in anchors if a not in covered and a not in unbudgeted][:20]}
    return picked, stats


def render(picked):
    """入选片段 → 提示词文本,每段带出处,便于模型在 evidence 里回指。"""
    return "\n\n".join(f"### 证据片段「{c['source']}」§{c['path'] or '正文'} (块{c['index']})\n{c['text']}" for c in picked)
