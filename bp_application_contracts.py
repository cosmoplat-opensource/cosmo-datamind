"""Explicit application diagnostics HTTP boundary — DR-071."""
from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from application_contracts.models import ContractError, require
from application_contracts.service import CheckService, validate
from application_contracts.adapters import registry as defaults, Resolver


def init_app(app, *, root, registry=None, resolver=None, enabled=True):
    registry = defaults() if registry is None else registry
    resolver = Resolver(root) if resolver is None else resolver
    bp = Blueprint('application_contracts', __name__)
    def service(write=False):
        if write and not enabled: raise ContractError('application_unavailable', '应用检查未启用。')
        return CheckService(root, registry=registry, resolver=resolver, read_only=not write)
    def body(fields):
        data = request.get_json()
        require(isinstance(data, dict) and set(data) == set(fields))
        return data
    @bp.errorhandler(ContractError)
    def failure(exc):
        status = {'not_found': 404, 'storage_unavailable': 503, 'application_unavailable': 503,
                  'request_conflict': 409, 'stale_result': 409, 'repair_unavailable': 409}.get(exc.code, 400)
        return jsonify(error={'code': exc.code, 'message': str(exc)}), status
    @bp.errorhandler(BadRequest)
    def malformed(exc): return failure(ContractError('invalid_request', '请求格式不正确。'))
    @bp.errorhandler(RequestEntityTooLarge)
    def oversized(exc): return jsonify(error={'code': 'file_too_large', 'message': '请求超过大小限制。'}), 413
    @bp.get('/api/applications')
    def applications():
        return jsonify(items=registry.descriptors())
    @bp.post('/api/application-checks')
    def check():
        data = body(('entrypoint', 'asset_refs', 'context', 'request_id'))
        validate(**data)
        spec = registry.resolve(data['entrypoint'])
        if not enabled or spec['availability'] != 'available':
            raise ContractError('application_unavailable', '应用暂不可用。')
        result = service(True).evaluate(**data)
        return jsonify(result), 200 if result['replayed'] else 201
    @bp.get('/api/application-checks/<check_id>')
    def read(check_id): return jsonify(service().read(check_id))
    @bp.post('/api/application-checks/<check_id>/repairs')
    def repair(check_id):
        from application_contracts.models import revision
        data = body(('issue_id', 'action_id', 'input', 'expected_revision', 'request_id'))
        require(revision(data['expected_revision']))
        result = service(True).repair(check_id=check_id, **data)
        return jsonify(result), 202 if result['kind'] == 'submit' else 200
    app.register_blueprint(bp)
