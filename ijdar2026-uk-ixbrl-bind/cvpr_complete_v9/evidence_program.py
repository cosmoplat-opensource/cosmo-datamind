"""Small grounded answer language; no arbitrary Python or filesystem execution."""
import ast
from decimal import Decimal, InvalidOperation, localcontext
import math
import re

SCALES = ('', 'percent', 'thousand', 'million', 'billion')


def numeric(value):
    if type(value) in (int, float):
        value = str(value)
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError('A short decimal string is required')
    value = value.strip()
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', value):
        raise ValueError('Numeric evidence must use a plain signed decimal')
    result = Decimal(value)
    if not result.is_finite() or result.adjusted() > 18 or result.adjusted() < -18:
        raise ValueError('Numeric evidence outside supported finite range')
    return result


def calculate(expression, values, evidence_ids=None):
    if not isinstance(expression, str) or len(expression) > 300:
        raise ValueError('Invalid arithmetic expression')
    tree = ast.parse(expression, mode='eval')
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError('Arithmetic expression too complex')
    used = set(); evidence_ids = set(values) if evidence_ids is None else set(evidence_ids)

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Name) and node.id in values:
            used.add(node.id); return values[node.id]
        if isinstance(node, ast.Constant) and type(node.value) is int and node.value in (0, 1, 100):
            return Decimal(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = visit(node.operand); return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): return a + b
            if isinstance(node.op, ast.Sub): return a - b
            if isinstance(node.op, ast.Mult): return a * b
            if b == 0: raise ValueError('Division by zero')
            return a / b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'count' and not node.keywords:
            if not node.args or any(not isinstance(n, ast.Name) or n.id not in evidence_ids for n in node.args):
                raise ValueError('Count requires distinct evidence identifiers')
            names = [n.id for n in node.args]
            if len(names) != len(set(names)): raise ValueError('Repeated evidence in count')
            used.update(names); return Decimal(len(names))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('sum', 'mean', 'min', 'max') and not node.keywords:
            if not node.args: raise ValueError('Aggregation needs grounded arguments')
            items = [visit(n) for n in node.args]
            if node.func.id == 'sum': return sum(items, Decimal(0))
            if node.func.id == 'mean': return sum(items, Decimal(0)) / len(items)
            if node.func.id == 'min': return min(items)
            return max(items)
        raise ValueError('Unsupported operation or ungrounded number')
    try:
        with localcontext() as ctx:
            ctx.prec = 28
            value = visit(tree)
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('Invalid finite arithmetic') from exc
    if not used or not value.is_finite() or value.adjusted() > 18:
        raise ValueError('Ungrounded or nonfinite calculation')
    return format(value, 'f'), sorted(used)


def execute(program, page_count):
    """Validate references/geometry and execute; semantics remain a model prediction."""
    if not isinstance(program, dict) or set(program) != {'evidence', 'operation', 'expression', 'answer_refs', 'scale'}:
        raise ValueError('Wrong program schema')
    if program['scale'] not in SCALES:
        raise ValueError('Unsupported answer scale')
    evidence = program['evidence']
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 24:
        raise ValueError('Evidence list outside fixed budget')
    ids = set(); numeric_values = {}; entries = {}
    for entry in evidence:
        required = {'id', 'page', 'box', 'text', 'value', 'scope'}
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValueError('Wrong evidence schema')
        ident = entry['id']
        if not isinstance(ident, str) or not re.fullmatch(r'e\d{1,2}', ident) or ident in ids:
            raise ValueError('Invalid or duplicate evidence identifier')
        ids.add(ident); entries[ident] = entry
        if type(entry['page']) is not int or not 0 <= entry['page'] < page_count:
            raise ValueError('Evidence page not in public input')
        box = entry['box']
        if not isinstance(box, list) or len(box) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in box):
            raise ValueError('Invalid evidence coordinates')
        if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise ValueError('Evidence box outside image')
        if not isinstance(entry['text'], str) or not 1 <= len(entry['text']) <= 500:
            raise ValueError('Invalid evidence span')
        if not isinstance(entry['scope'], dict) or set(entry['scope']) != {'row', 'period', 'unit', 'scale', 'entity', 'dimension', 'version'}:
            raise ValueError('Wrong scope component schema')
        if any(not isinstance(v, str) or len(v) > 200 for v in entry['scope'].values()):
            raise ValueError('Invalid scope text')
        if entry['value'] is not None:
            numeric_values[ident] = numeric(entry['value'])
    if program['operation'] == 'arithmetic':
        if program['answer_refs'] != []:
            raise ValueError('Arithmetic output must use expression')
        value, used = calculate(program['expression'], numeric_values, ids)
        answer = {'answer': value, 'scale': program['scale']}
    elif program['operation'] == 'extract':
        refs = program['answer_refs']
        if program['expression'] != '' or not isinstance(refs, list) or not refs or len(refs) != len(set(refs)) or any(r not in entries for r in refs):
            raise ValueError('Invalid extract references')
        answer = {'answer': [entries[r]['text'] for r in refs], 'scale': program['scale']}; used = refs
    else:
        raise ValueError('Unsupported operation')
    return {'answer': answer, 'used_evidence': used,
        'geometry_validated': True, 'visual_grounding_independently_certified': False,
        'scope_semantics_independently_certified': False}


def scope_warnings(program):
    """Soft component checks available to every agent control; unknown is retained."""
    e = program['evidence']; warnings = []
    if any(any(v in ('', 'unknown') for v in r['scope'].values()) for r in e):
        warnings.append('Some scope components are unknown; do not treat them as certified matches.')
    if program['operation'] == 'arithmetic':
        values = {r['id']: numeric(r['value']) for r in e if r['value'] is not None}
        _, used = calculate(program['expression'], values, {r['id'] for r in e})
        selected = [r for r in e if r['id'] in used]
        for field in ('unit', 'scale', 'entity', 'dimension', 'version'):
            known = {r['scope'][field] for r in selected} - {'', 'unknown'}
            if len(known) > 1:
                warnings.append('Operand scope differs in ' + field + ': ' + ', '.join(sorted(known)) + '. Verify whether the requested operation permits this.')
    return warnings
