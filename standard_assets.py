#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线标准本体资产的清单、完整性指纹和只读术语索引。

只解析 ``ontology/standards`` 中清单声明的文件。RDF 的 ``owl:imports`` 与 XSD 的
``import/include`` 不会被跟随，因而目录 API 和构建过程都不会隐式访问网络。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os

import srv_context
import xml.etree.ElementTree as ET


HERE = os.path.dirname(os.path.abspath(__file__))
STANDARD_ROOT = os.path.join(HERE, "ontology", "standards")
MANIFEST_PATH = os.path.join(STANDARD_ROOT, "manifest.json")
_CACHE: dict[tuple, dict] = {}


def clear_cache():
    """测试或运维替换资产后清空进程内解析缓存。"""
    _CACHE.clear()


def _manifest():
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        value = json.load(fh)
    if value.get("schema_version") != 1 or not isinstance(value.get("assets"), dict):
        raise ValueError("标准资产 manifest 格式无效")
    return value


def _confined(pattern):
    if not isinstance(pattern, str) or not pattern or ".." in pattern:
        raise ValueError("标准资产路径无效")
    return srv_context.confine(STANDARD_ROOT, pattern)   # 路径限定单一实现


def _files(spec):
    paths = []
    for pattern in spec.get("files") or []:
        resolved = _confined(pattern)
        matches = sorted(glob.glob(resolved)) if glob.has_magic(pattern) else [resolved]
        for path in matches:
            if os.path.isfile(path) and path not in paths:
                paths.append(path)
    return paths


def _signature(paths):
    return tuple((path, stat.st_size, stat.st_mtime_ns)
                 for path in paths for stat in [os.stat(path)])


def _fingerprint(paths):
    digest = hashlib.sha256()
    for path in paths:
        rel = os.path.relpath(path, STANDARD_ROOT).replace(os.sep, "/")
        digest.update(rel.encode("utf-8")); digest.update(b"\0")
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(131072), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _rdf_index(paths):
    from rdflib import Graph, OWL, RDF, RDFS, SKOS, URIRef

    graph = Graph()
    errors = []
    for path in paths:
        try:
            fmt = "turtle" if path.lower().endswith(".ttl") else "xml"
            graph.parse(path, format=fmt)
        except Exception as exc:
            errors.append(f"{os.path.basename(path)}: {str(exc)[:160]}")
    classes = {s for s in graph.subjects(RDF.type, OWL.Class)}
    classes.update(graph.subjects(RDF.type, RDFS.Class))
    properties = set()
    for kind in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty):
        properties.update(graph.subjects(RDF.type, kind))
    labels = {}
    for predicate in (RDFS.label, SKOS.prefLabel):
        for subject, label in graph.subject_objects(predicate):
            if isinstance(subject, URIRef):
                text = str(label).strip()
                if text and len(text) <= 120:
                    labels.setdefault(str(subject), text)
    terms = sorted(set(labels.values()), key=lambda value: (value.lower(), value))
    return {"triples": len(graph), "classes": len(classes), "properties": len(properties),
            "terms": terms, "errors": errors}


def _xsd_index(paths):
    names, elements, types, errors = set(), set(), set(), []
    for path in paths:
        try:
            root = ET.parse(path).getroot()
            for node in root.iter():
                local = node.tag.rsplit("}", 1)[-1]
                name = str(node.attrib.get("name") or "").strip()
                if not name or len(name) > 160:
                    continue
                if local in {"element", "complexType", "simpleType", "group", "attributeGroup"}:
                    names.add(name)
                if local == "element":
                    elements.add(name)
                if local in {"complexType", "simpleType"}:
                    types.add(name)
        except (ET.ParseError, OSError) as exc:
            errors.append(f"{os.path.basename(path)}: {str(exc)[:160]}")
    return {"triples": 0, "classes": len(types), "properties": len(elements),
            "terms": sorted(names, key=lambda value: (value.lower(), value)), "errors": errors}


def status(asset_id):
    """返回单个标准资产的可序列化状态；任何解析失败都明确体现为 ``ready=false``。"""
    manifest = _manifest()
    spec = manifest["assets"].get(asset_id)
    if not isinstance(spec, dict):
        raise KeyError(asset_id)
    paths = _files(spec)
    expected_patterns = len(spec.get("files") or [])
    signature = _signature(paths)
    cache_key = (asset_id, signature)
    if cache_key in _CACHE:
        return json.loads(json.dumps(_CACHE[cache_key], ensure_ascii=False))

    errors = []
    if not paths:
        errors.append("清单声明的文件不存在")
    elif len(paths) < expected_patterns:
        errors.append("部分清单文件或文件模式没有匹配到资产")
    parsed = {"triples": 0, "classes": 0, "properties": 0, "terms": [], "errors": []}
    if paths:
        try:
            parsed = _xsd_index(paths) if spec.get("format") == "xsd" else _rdf_index(paths)
        except Exception as exc:
            parsed["errors"] = [str(exc)[:200]]
    errors.extend(parsed.get("errors") or [])
    result = {
        "id": asset_id,
        "name": spec.get("name") or asset_id,
        "version": spec.get("version") or "",
        "format": spec.get("format") or "",
        "installed": bool(paths),
        "ready": bool(paths) and not errors,
        "local": True,
        "file_count": len(paths),
        "bytes": sum(os.path.getsize(path) for path in paths),
        "files": [os.path.relpath(path, HERE).replace(os.sep, "/") for path in paths],
        "fingerprint": _fingerprint(paths) if paths else "",
        "triples": int(parsed.get("triples") or 0),
        "class_count": int(parsed.get("classes") or 0),
        "property_count": int(parsed.get("properties") or 0),
        "term_count": len(parsed.get("terms") or []),
        "sample_terms": (parsed.get("terms") or [])[:40],
        "source": spec.get("source") or "",
        "source_revision": spec.get("source_revision") or "",
        "license": spec.get("license") or "",
        "attribution": spec.get("attribution") or "",
        "errors": errors,
    }
    # 每个资产最多保留当前版本，避免热替换时积累旧索引，同时不驱逐其它标准的缓存。
    for old_key in [key for key in _CACHE if key[0] == asset_id]:
        _CACHE.pop(old_key, None)
    _CACHE[cache_key] = result
    return json.loads(json.dumps(result, ensure_ascii=False))


def catalog_status():
    try:
        ids = list((_manifest().get("assets") or {}).keys())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"_error": str(exc)[:200]}
    out = {}
    for asset_id in ids:
        try:
            out[asset_id] = status(asset_id)
        except Exception as exc:
            out[asset_id] = {"id": asset_id, "installed": False, "ready": False,
                             "local": True, "errors": [str(exc)[:200]]}
    return out


def trace(asset_id):
    """构建 manifest 使用的精简资产追踪信息。"""
    item = status(asset_id)
    return {key: item.get(key) for key in (
        "id", "name", "version", "ready", "file_count", "fingerprint",
        "source_revision", "license", "term_count", "class_count", "property_count",
    )}


def prompt_context(asset_id, max_terms=24):
    """给模型的本地标准资产事实；只给有限术语样本，避免把整个标准塞入提示。"""
    item = status(asset_id)
    if not item["ready"]:
        reason = "；".join(item.get("errors") or ["未知错误"])
        return f"本地标准资产未就绪（{reason}）；不得声称已经按该标准完成对齐。"
    terms = "、".join((item.get("sample_terms") or [])[:max_terms]) or "无可展示术语"
    counts = (f"{item['triples']} 三元组" if item.get("triples") else
              f"{item['class_count']} 类型 / {item['property_count']} 元素")
    return (f"本地机器可读资产已加载：{item['name']} {item['version']}，"
            f"{item['file_count']} 个文件，{counts}，内容指纹 {item['fingerprint'][:12]}。\n"
            f"可核对术语样本：{terms}。只可复用资产中存在且与当前证据相符的术语。")
