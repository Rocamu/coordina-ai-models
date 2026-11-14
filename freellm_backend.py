"""Explicit, allowlisted free NVIDIA routes through FreeLLMAPI Desktop."""
import json
import os
from pathlib import Path
import signal
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:31415'
PROVIDER = 'nvidia'
MODEL = 'nvidia/nemotron-3-ultra-550b-a55b'
# NVIDIA Build pages verified 2026-11-08: all three show Free Endpoint Available.
ALLOWED_MODELS = (MODEL, 'nvidia/nemotron-3.5-lightning-30b-a3b', 'openai/gpt-oss-20b')
SECRETS = Path.home()/'.config/agent-ecosystem/secrets'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirección rechazada; credencial no reenviada.')


def credential(name):
    path = SECRETS/name
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('Falta una credencial privada de FreeLLMAPI (0600).')
    return path.read_text().strip()


def request(path, payload=None, timeout=8, admin=False, request_id=None):
    key = credential('freellmapi-dashboard.token' if admin else 'freellmapi.key')
    headers = {'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'}
    if request_id: headers['Idempotency-Key'] = request_id
    req = urllib.request.Request(BASE+path, headers=headers,
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(8*1024*1024+1)
        if len(raw) > 8*1024*1024: raise ValueError('Respuesta demasiado grande.')
        return json.loads(raw), dict(response.headers), response.status


def validate_model(model=None):
    selected = MODEL if model is None else model
    if not isinstance(selected, str) or selected not in ALLOWED_MODELS:
        raise ValueError('Modelo fuera de la lista gratuita NVIDIA aprobada.')
    return selected


def validate_policy(models, keys, model=None):
    selected = validate_model(model)
    candidates = [m for m in models if m.get('platform') == PROVIDER and m.get('modelId') == selected]
    if len(candidates) != 1 or not candidates[0].get('enabled') or candidates[0].get('endpointScope'):
        raise ValueError('El modelo NVIDIA solicitado no está habilitado de forma inequívoca.')
    active = [k for k in keys if k.get('enabled')]
    if len(active) != 1 or active[0].get('platform') != PROVIDER:
        raise ValueError('Se requiere una sola clave NVIDIA activa; no hay fallback a otras claves.')
    scope = active[0].get('modelScope')
    if not isinstance(scope, list) or not scope or any(m not in ALLOWED_MODELS for m in scope) or selected not in scope:
        raise ValueError('La clave debe limitarse a la lista gratuita aprobada e incluir el modelo solicitado.')
    return [m for m in ALLOWED_MODELS if m in scope and any(
        row.get('platform') == PROVIDER and row.get('modelId') == m and row.get('enabled')
        and not row.get('endpointScope') for row in models)]


def status(model=None):
    selected = validate_model(model)
    models, _, _ = request('/api/models', admin=True)
    keys, _, _ = request('/api/keys', admin=True)
    available = validate_policy(models, keys, selected)
    # Installed server resolves provider:model to an exact member, then passes a
    # chain restricted to that member. Plain model aliases can merge providers.
    unify, _, _ = request('/api/settings/unify', admin=True)
    if unify.get('enabled') is not True:
        raise ValueError('FreeLLM requiere unificación activa para fijar proveedor:modelo sin sustituciones.')
    guards, _, _ = request('/api/settings/guardrails', admin=True)
    if guards.get('maxConsecutiveUpstreamFails') != 1:
        raise ValueError('El límite de un fallo debe estar activo; no se inicia la consulta.')
    return dict(provider=PROVIDER, model=selected, models=available,
                selector=PROVIDER+':'+selected, base_url=BASE+'/v1',
                tools_enabled=False, cost_usd=0, paid_fallback=False, timeout_seconds=120)


def generate(jid, prompt, timeout=120, model=None):
    selected = validate_model(model)
    if not prompt.strip() or len(prompt.encode()) > 240000:
        raise ValueError('Entrada vacía o mayor de 240000 bytes.')
    if not 1 <= timeout <= 120: raise ValueError('Límite máximo: 120 segundos.')
    started = time.monotonic()
    status(selected)
    remaining = timeout-(time.monotonic()-started)
    if remaining <= 0: raise TimeoutError()
    data, headers, http = request('/v1/chat/completions', {
        'model': PROVIDER+':'+selected, 'messages': [{'role': 'user', 'content': prompt}],
        'stream': False, 'max_tokens': 16384, 'reasoning_effort': 'none'},
        timeout=remaining, request_id='coordina-freellm-'+jid)
    routed = next((v for k,v in headers.items() if k.lower() == 'x-routed-via'), None)
    if routed != PROVIDER+'/'+selected or data.get('model') != selected:
        raise ValueError('Proveedor o modelo real distinto del autorizado; respuesta rechazada.')
    choice = data.get('choices', [{}])[0]
    answer = choice.get('message', {}).get('content') or ''
    if choice.get('finish_reason') != 'stop' or not answer.strip():
        raise ValueError('Respuesta incompleta; no se reintenta automáticamente.')
    return dict(answer=answer, metadata=dict(provider=PROVIDER, model=selected,
        selector=PROVIDER+':'+selected,
        returned_model=data['model'], routed_via=routed, http=http,
        seconds=round(time.monotonic()-started, 3), usage=data.get('usage'),
        cost_usd=0, cost_basis='configured NVIDIA free trial route',
        tools_enabled=False, paid_fallback=False))


def main():
    os.umask(0o077)
    def expired(*_): raise TimeoutError()
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(120)
    try:
        result = status() if sys.argv[1:] == ['status'] else generate(sys.argv[1], sys.stdin.read(), min(120,int(sys.argv[2])),
            model=sys.argv[4] if len(sys.argv)>4 else None)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(json.dumps({'error': message}), file=sys.stderr)
        sys.exit(1)
    finally:
        signal.alarm(0)


if __name__ == '__main__': main()
