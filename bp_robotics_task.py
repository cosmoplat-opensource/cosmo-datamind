"""Task, completion and optional OWL import HTTP composition — DR-063 / DR-066."""
import io
import sqlite3

from flask import Blueprint, jsonify, request, send_file
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from domain_runs.build_service import BuildRequestError
from domain_runs.robotics_task_service import TaskError, TaskService
from domains.robotics.task_model import ModelClient, ModelError


def init_app(app, runtime):
    bp = Blueprint('robotics_task', __name__)
    if runtime.enabled:
        TaskService(runtime.root)

    def service(*, write=True):
        if write:
            runtime.writer()
        if not (runtime.root / 'domain.sqlite3').is_file():
            raise TaskError('not_found', '尚无任务记录。')
        return TaskService(runtime.root, generation_policy='offline')

    def body(required, optional=()):
        data = request.get_json()
        if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
            raise TaskError('invalid_request', '请求字段不完整或包含不支持的字段。')
        return data

    @bp.errorhandler(TaskError)
    @bp.errorhandler(ModelError)
    def task_error(exc):
        status = {'not_found': 404, 'session_conflict': 409, 'request_conflict': 409, 'task_busy': 409,
            'stale_result': 409, 'no_candidate': 409, 'artifact_integrity': 409, 'save_failed': 503,
            'invalid_answer': 422, 'invalid_ontology': 422}.get(exc.code, 400)
        return jsonify(error={'code': exc.code, 'message': str(exc)}), status

    @bp.errorhandler(BuildRequestError)
    def build_error(exc):
        return jsonify(error={'code': exc.code, 'message': str(exc)}), exc.status_code

    @bp.errorhandler(BadRequest)
    def malformed(exc):
        return task_error(TaskError('invalid_request', '请求格式不正确。'))

    @bp.errorhandler(RequestEntityTooLarge)
    def too_large(exc):
        return jsonify(error={'code': 'file_too_large', 'message': '文件超过大小限制。'}), 413

    @bp.errorhandler(OSError)
    @bp.errorhandler(sqlite3.Error)
    def unavailable(exc):
        return jsonify(error={'code': 'storage_unavailable', 'message': '存储暂不可用，请稍后重试。'}), 503

    @bp.get('/api/robotics/task-capabilities')
    def capabilities():
        return jsonify(enabled=runtime.enabled, model=ModelClient.from_datamind().status())

    @bp.post('/api/robotics/tasks')
    def start():
        store = service()
        return jsonify(store.start(**body(('ontology_id', 'version_id', 'mode', 'request_id')))), 201

    @bp.get('/api/robotics/tasks/<session_id>')
    def get(session_id):
        return jsonify(service(write=False).get(session_id))

    @bp.post('/api/robotics/tasks/<session_id>/messages')
    def message(session_id):
        return jsonify(service().message(session_id, **body(('text', 'expected_revision', 'request_id'))))

    @bp.post('/api/robotics/tasks/<session_id>/completion')
    def complete(session_id):
        return jsonify(service().complete(session_id, **body(('answers', 'expected_revision', 'request_id'))))

    @bp.post('/api/robotics/tasks/<session_id>/generate')
    def generate(session_id):
        return jsonify(service().generate(session_id, **body(('expected_revision', 'request_id'))))

    @bp.post('/api/robotics/tasks/<session_id>/notes')
    def notes(session_id):
        return jsonify(service().save_notes(session_id, **body(('notes', 'expected_revision', 'request_id'))))

    @bp.post('/api/robotics/tasks/<session_id>/choices')
    def choose(session_id):
        return jsonify(service().choose(session_id, **body(('field', 'expected_revision', 'request_id'), ('target', 'create'))))

    @bp.post('/api/robotics/tasks/<session_id>/configurations')
    def configure(session_id):
        return jsonify(service().configure(session_id,
            **body(('field', 'expected_revision', 'request_id'), ('target', 'create'))))

    @bp.get('/api/robotics/tasks/<session_id>/candidate')
    def download(session_id):
        if set(request.args) != {'revision'} or len(request.args.getlist('revision')) != 1 or not request.args['revision'].isdigit():
            raise TaskError('invalid_request', '需要当前任务修订号。')
        content = service(write=False).read_candidate(session_id, expected_revision=int(request.args['revision']))
        return send_file(io.BytesIO(content), mimetype='application/xml', as_attachment=True,
                         download_name='behavior-tree.xml', max_age=0)

    @bp.post('/api/robotics/imports')
    def import_owl():
        store = service()
        if set(request.files) != {'file'} or len(request.files.getlist('file')) != 1 or set(request.form) != {'name', 'mode', 'request_id'}:
            raise TaskError('invalid_request', '请选择一个 OWL 文件并填写名称。')
        if any(len(request.form.getlist(k)) != 1 for k in request.form):
            raise TaskError('invalid_request', '重复的表单字段。')
        content = request.files['file'].stream.read(runtime.limits['file_bytes'] + 1)
        if len(content) > runtime.limits['file_bytes']:
            return too_large(None)
        return jsonify(store.import_owl(content=content, **dict(request.form))), 201

    @bp.post('/api/robotics/task-demo')
    def demo():
        store = service()
        args = body(('request_id',))
        from domain_runs.robotics_task_demo import build_example
        # This endpoint accepts no user asset to supplement: it always builds packaged examples.
        version = build_example(runtime.root, mode='demo')
        return jsonify(store.start(ontology_id=version['ontology_id'], version_id=version['version_id'],
                                   mode='demo', request_id=args['request_id'])), 201

    app.register_blueprint(bp)
