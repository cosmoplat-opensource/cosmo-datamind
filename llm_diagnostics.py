"""Allowlisted per-request LLM diagnostics; never persist provider text or credentials — DR-029 / DR-070."""
import urllib.error

CODES={'model_http_error','model_timeout','model_connection_failed','model_invalid_response',
       'model_empty_content','model_runtime_error','model_unconfigured','model_success'}
NUMBERS={'http_status','elapsed_ms','max_tokens','prompt_tokens','completion_tokens','total_tokens',
         'reasoning_tokens','content_chars','reasoning_chars'}
FINISH={'stop','length','content_filter','tool_calls','function_call','max_tokens','max_output_tokens','error'}


def safe_diagnostic(value):
    if not isinstance(value,dict):return {'code':'model_runtime_error'}
    result={'code':value.get('code') if value.get('code') in CODES else 'model_runtime_error'}
    for key in NUMBERS:
        v=value.get(key)
        if type(v) is int and 0<=v<=10**12:result[key]=v
    if 'finish_reason' in value:
        result['finish_reason']=value['finish_reason'] if value['finish_reason'] in FINISH else 'unknown'
    return result


def transport_diagnostic(exc):
    if isinstance(exc,urllib.error.HTTPError):return safe_diagnostic({'code':'model_http_error','http_status':exc.code})
    if isinstance(exc,TimeoutError) or isinstance(getattr(exc,'reason',None),TimeoutError):return {'code':'model_timeout'}
    if isinstance(exc,(urllib.error.URLError,ConnectionError,OSError)):return {'code':'model_connection_failed'}
    return {'code':'model_runtime_error'}


def describe(value):
    d=safe_diagnostic(value);code=d['code']
    if code=='model_http_error':return '模型服务返回 HTTP '+str(d.get('http_status','错误'))+'。'
    if code=='model_timeout':return '等待模型响应超时。'
    if code=='model_connection_failed':return '模型连接失败或连接中断。'
    if code=='model_invalid_response':return '模型服务返回的响应结构无效。'
    if code=='model_unconfigured':return '模型端点或凭据未配置。'
    if code=='model_empty_content':
        message='模型返回空正文'
        if d.get('finish_reason') in ('length','max_tokens','max_output_tokens'):
            message+='，达到输出令牌上限'
            if d.get('reasoning_tokens',0)>0:message+='（其中包含思考令牌）'
        return message+'。'
    return '模型调用未成功；未返回可用的正文。'
