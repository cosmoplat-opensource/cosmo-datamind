"""Bounded shared XML intake before public build admission — DR-070 / IR-024 T03.

This checks source roles and parser limits, not the fixed UR execution template.
The legacy worker still checks its full domain contract, including old archives.
"""
import io
import xml.etree.ElementTree as ET

from .source_validation import SourceValidationError, issue

MAX_NODES = 20_000
MAX_DEPTH = 100


def validate_source(content, *, role):
    def fail(code, message, **location):
        raise SourceValidationError([issue(code, message, role, **location)])

    if role not in ('behavior_tree', 'node_models'):
        fail('invalid_source_role', '未知的 XML 来源角色。')
    # NUL removal also catches declarations encoded as UTF-16/32, before parsing.
    upper = content.upper().replace(b'\x00', b'')
    if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper:
        fail('forbidden_xml_declaration', 'XML 不允许 DTD 或实体声明。')
    depth, count, root = 0, 0, None
    try:
        for event, node in ET.iterparse(io.BytesIO(content), events=('start', 'end')):
            if event == 'start':
                depth += 1
                count += 1
                if root is None:
                    root = node
                if count > MAX_NODES or depth > MAX_DEPTH:
                    fail('xml_limit', 'XML 超过 20000 个节点或 100 层嵌套限制。')
            else:
                depth -= 1
    except (ET.ParseError, UnicodeError, ValueError, LookupError) as exc:
        if isinstance(exc, SourceValidationError):
            raise
        position = getattr(exc, 'position', None)
        location = {'line': position[0], 'column': position[1]} if position else {}
        fail('invalid_xml', 'XML 无法解析，请检查格式与编码。', **location)
    if root is None or root.tag != 'root':
        fail('invalid_source_role', '需要行为树格式的 root 根元素。')
    if role == 'behavior_tree' and not root.findall('BehaviorTree'):
        fail('invalid_source_role', '主行为树 XML 缺少 BehaviorTree。')
    if role == 'node_models' and (not root.findall('TreeNodesModel') or root.findall('BehaviorTree')):
        fail('invalid_source_role', '节点模型 XML 需要 TreeNodesModel，不能混用主行为树文件。')
