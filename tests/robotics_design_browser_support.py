"""Owned real server with a local synthetic model, never fake application HTTP — DR-070."""
import json
from urllib.request import Request, urlopen
import uuid

from tests.robotics_browser_support import LiveSite
from tests.robotics_modeling_support import sources


def model_reply(proposal):
    return {'body': json.dumps({'choices': [{'message': {'role': 'assistant',
        'content': json.dumps(proposal, ensure_ascii=False)}}]}, ensure_ascii=False).encode()}


class DesignSite(LiveSite):
    def __init__(self, directory, output, *, model_url=None, incomplete=False, prepare=None):
        self.model_url, self.incomplete, self.prepare = model_url, incomplete, prepare
        self.prepared = None
        super().__init__(directory, output)

    def start(self):
        if self.model_url:
            self.env.update(DATAMIND_LLM_BASE=self.model_url, DATAMIND_LLM_MODEL='ir024-test-model',
                DATAMIND_LLM_KEY='ir024-local-only', DATAMIND_LLM_TIMEOUT='5', CLAW_DRIVER='openai')
            if self.incomplete:
                self.env.pop('DATAMIND_LLM_KEY', None)
        if self.prepare is not None:
            callback, self.prepare = self.prepare, None
            self.prepared = callback(self.root)
        super().start()

    def post_json(self, path, body):
        request = Request(self.url + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=10) as reply:
            return reply.status, json.load(reply)

    def admit(self, description='设计技能与参数', request_id=None):
        boundary = 'design-test-' + uuid.uuid4().hex
        fields = {'name': '设计候选', 'recipe': 'bt_xml', 'profile': 'ur_pick_place',
                  'request_id': request_id or uuid.uuid4().hex, 'description': description}
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in fields.items()]
        for role, raw in sources().items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{role}"; filename="{role}.xml"\r\nContent-Type: application/xml\r\n\r\n'.encode() + raw + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        request = Request(self.url + '/api/robotics/builds', data=b''.join(parts),
            headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        with urlopen(request, timeout=10) as reply:
            assert reply.status == 202
            return json.load(reply)

    def design_posts(self):
        return [r for r in self.evidence['requests'] if r['method'] == 'POST'
                and ('/api/robotics/builds' in r['url'] or '/api/robotics/design-sessions/' in r['url'])]
