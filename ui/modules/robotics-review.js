/* Fixed-version knowledge editing and review state — DR-059 / DR-061 / DR-063 / DR-064. */
(function (global) {
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  async function json(fetcher, url, options) {
    const response = await fetcher(url, options);
    let body;
    try { body = await response.json(); } catch (_) { throw Object.assign(new Error('响应不是 JSON'), {status:response.status}); }
    if (!response.ok) throw Object.assign(new Error(body.error?.message || '请求失败'), {status:response.status, code:body.error?.code});
    return body;
  }
  function createController(options = {}) {
    const fetcher = options.fetch || global.fetch.bind(global), emit = options.onChange || (() => {});
    const requestId = options.requestId || (() => global.crypto.randomUUID());
    let dead = false, generation = 0, edits = 0, identity = {actor:'',reason:''}, retry = null;
    const state = {enabled:false, knowledge:null, operations:[], dirty:false, busy:false, error:null, review:null, diff:null, mergePreview:null};
    const snapshot = () => copy(state), notify = () => { if (!dead) emit(snapshot()); };
    const fail = e => { state.error = {message:e.message || String(e), status:e.status, code:e.code}; };
    const base = () => '/api/ontology-assets/' + encodeURIComponent(state.knowledge.ontology_id);
    const versionUrl = () => base() + '/versions/' + encodeURIComponent(state.knowledge.version_id);
    const condition = () => ({base_version_id:state.knowledge.version_id,base_payload_sha256:state.knowledge.payload_sha256,
      expected_head_version_id:state.knowledge.head.version_id,expected_revision:state.knowledge.head.revision});
    function guard(clean = false) {
      if (dead || state.busy) return false;
      if (!state.enabled || !state.knowledge || !identity.actor.trim() || !identity.reason.trim() || (clean && state.dirty)) {
        fail(new Error(!state.enabled ? '机器人功能未启用' : clean && state.dirty ? '请先保存或放弃未保存修改' : '请填写操作者署名和操作理由')); notify(); return false;
      }
      return true;
    }
    async function load(oid, vid, token) {
      const body = await json(fetcher, '/api/ontology-assets/' + encodeURIComponent(oid) + '/versions/' + encodeURIComponent(vid) + '/knowledge');
      if (dead || token !== generation) return false;
      if (body.ontology_id !== oid || body.version_id !== vid) throw new Error('返回的本体或版本身份不匹配');
      state.knowledge = body;
      state.review = (body.reviews || []).filter(r => r.version_id === vid && r.payload_sha256 === body.payload_sha256).at(-1) || null;
      return true;
    }
    async function post(url, payload, mode) {
      const token = generation, revision = edits;
      const fingerprint = JSON.stringify([url, payload]);
      if (!retry || retry.fingerprint !== fingerprint) retry = {fingerprint, id:requestId()};
      const body = {...payload, request_id:retry.id};
      state.busy = true; state.error = null; notify();
      try {
        const result = await json(fetcher, url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        if (dead || token !== generation) return null;
        if (mode === 'review') state.review = result;
        if (mode === 'version') {
          await load(result.ontology_id, result.version_id, token);
          if (dead || token !== generation) return null;
          if (revision === edits) { state.operations = []; state.dirty = false; }
          state.mergePreview = null; state.diff = null;
          await (options.onVersion || (() => {}))(copy(result));
        }
        if (mode === 'publish') await (options.onVersion || (() => {}))(copy(result));
        retry = null;
        return result;
      } catch (e) { if (!dead && token === generation) fail(e); return null; }
      finally { if (!dead && token === generation) { state.busy = false; notify(); } }
    }
    const api = {
      snapshot,
      async init() { try { state.enabled = (await json(fetcher, '/api/robotics/capabilities')).enabled === true; }
        catch(e) { state.enabled = false; fail(e); } notify(); },
      async openVersion(oid, vid, {discard = false} = {}) {
        if (state.dirty && !discard) return false;
        const token = ++generation;
        state.knowledge = null; state.review = null; state.operations = []; state.dirty = false;
        state.error = null; state.busy = false; state.diff = null; state.mergePreview = null; retry = null; edits++; notify();
        try { await load(oid, vid, token); }
        catch(e) { if (!dead && token === generation) fail(e); }
        if (!dead && token === generation) notify();
        return !state.error;
      },
      setIdentity(value) { identity = {actor:String(value.actor || ''),reason:String(value.reason || '')}; },
      setOperations(value) { state.operations = copy(value); state.dirty = value.length > 0; edits++; notify(); },
      async save() { if (!guard() || !state.operations.length) return null;
        return post(base() + '/drafts', {...condition(),...identity,operations:copy(state.operations)}, 'version'); },
      async review(decision) { if (!guard(true)) return null;
        return post(versionUrl() + '/reviews', {...identity,payload_sha256:state.knowledge.payload_sha256,decision}, 'review'); },
      async publish() {
        if (!guard(true)) return null;
        const r = state.review, k = state.knowledge;
        if (!r || r.decision !== 'accepted' || r.version_id !== k.version_id || r.payload_sha256 !== k.payload_sha256) {
          fail(new Error('请先接受当前固定版本的审核')); notify(); return null;
        }
        return post(versionUrl() + '/publish', {...identity,review_id:r.review_id,payload_sha256:k.payload_sha256,
          expected_head_version_id:k.head.version_id,expected_revision:k.head.revision}, 'publish');
      },
      async compare(first, last) { if (!state.knowledge) return; const token = generation;
        try { const result = await json(fetcher, base() + '/diff?base=' + encodeURIComponent(first) + '&target=' + encodeURIComponent(last));
          if (!dead && token === generation) { state.diff = result; state.error = null; notify(); } }
        catch(e) { if (!dead && token === generation) { fail(e); notify(); } } },
      async previewMerge(inputs) {
        if (!guard(true)) return null;
        const token = generation; state.busy = true; state.error = null; notify();
        try { const result = await json(fetcher, base() + '/merge-previews', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(inputs)});
          if (!dead && token === generation) { state.mergePreview = result; return result; } }
        catch(e) { if (!dead && token === generation) fail(e); }
        finally { if (!dead && token === generation) { state.busy = false; notify(); } }
        return null;
      },
      async merge(decisions) { if (!guard(true) || !state.mergePreview) return null;
        return post(base() + '/merges', {...identity,merge_token:state.mergePreview.merge_token,inputs:state.mergePreview.inputs,decisions}, 'version'); },
      async editEvidence(suffix, extra) { if (!guard(true)) return null;
        return post(base() + '/' + suffix, {...condition(),...identity,...extra}, 'version'); },
      async revoke(evidence_iri, kind = 'revoked') { if (!guard(true)) return null;
        const result = await post(base() + '/evidence-revocations', {...identity,evidence_iri,version_id:state.knowledge.version_id,kind}, 'record');
        if (result) { const token = generation; try { await load(state.knowledge.ontology_id,state.knowledge.version_id,token); } catch(e) { fail(e); } notify(); }
        return result;
      },
      dispose() { dead = true; generation++; }
    };
    return api;
  }
  global.RoboticsReview = {createController};
})(window);
