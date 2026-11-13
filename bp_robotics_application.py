"""Explicit robotics application preparation and confirmation HTTP boundary — DR-072."""
from flask import Blueprint, jsonify, request
import sqlite3
from werkzeug.exceptions import BadRequest
from domain_runs.robotics_application_service import ApplicationService, ApplicationError, require


def init_app(app,runtime):
    bp=Blueprint('robotics_application',__name__)
    def service(write=False):
        if write: runtime.writer()
        return ApplicationService(runtime.root,read_only=not write)
    def body(fields,optional=()):
        data=request.get_json();require(isinstance(data,dict) and set(fields)<=set(data)<=set(fields)|set(optional))
        if 'expected_revision' in data:require(type(data['expected_revision']) is int and data['expected_revision']>=0)
        return data
    @bp.errorhandler(ApplicationError)
    def failure(exc):
        status={'not_found':404,'storage_unavailable':503,'request_conflict':409,'revision_conflict':409,
            'head_conflict':409,'confirmation_required':409,'session_conflict':409,'role_conflict':409,'mode_conflict':409}.get(exc.code,400)
        detail={'code':exc.code,'message':str(exc)}
        if exc.issues is not None:detail['issues']=exc.issues
        return jsonify(error=detail),status
    @bp.errorhandler(BadRequest)
    def bad(exc):return failure(ApplicationError('invalid_request','请求格式不正确。'))
    @bp.errorhandler(KeyError)
    def absent(exc):return failure(ApplicationError('not_found','指定版本或适配记录不存在。'))
    @bp.errorhandler(ValueError)
    def integrity(exc):return jsonify(error={'code':'artifact_integrity','message':'设计来源或应用工件无效。'}),409
    @bp.errorhandler(OSError)
    @bp.errorhandler(sqlite3.Error)
    def storage(exc):return failure(ApplicationError('storage_unavailable','适配存储暂不可用。'))
    prefix='/api/robotics/application-adaptations'
    @bp.post(prefix)
    def prepare():
        data=body(('ontology_id','version_id','mode','task_ref','request_id'))
        state=service(True).prepare(**data,automatic=True)
        return jsonify(state),202 if state['status']=='queued' else 201
    @bp.get(prefix+'/<aid>')
    def read(aid):return jsonify(service().get(aid))
    @bp.post(prefix+'/<aid>/confirm-roles')
    def confirm(aid):
        data=body(('bindings','expected_revision','request_id'))
        return jsonify(service(True).confirm_roles(aid,**data))
    @bp.post(prefix+'/<aid>/submit')
    def submit(aid):
        data=body(('expected_revision','request_id'),optional=('bindings',))
        if 'bindings' in data:require(isinstance(data['bindings'],list))
        return jsonify(service(True).submit(aid,**data)),202
    @bp.post(prefix+'/<aid>/rebase')
    def rebase(aid):
        data=body(('asset_ref','expected_revision','request_id'))
        return jsonify(service(True).rebase(aid,**data))
    @bp.post(prefix+'/<aid>/resume-task')
    def resume(aid):
        data=body(('expected_revision','expected_task_revision','request_id'))
        return jsonify(service(True).resume_task(aid,**data))
    # Blueprint-local BuildRequestError guard is shared without widening any host permission.
    from domain_runs.build_service import BuildRequestError
    @bp.errorhandler(BuildRequestError)
    def rejected(exc):return jsonify(error={'code':exc.code,'message':str(exc)}),exc.status_code
    app.register_blueprint(bp)
