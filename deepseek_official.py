"""Official API, one request, durable audited cumulative budget. No tools."""
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request

MODEL = 'deepseek-flash'
CAP = 10_000_000_000  # legacy/default nanodollars; persisted policy is authoritative
INPUT_RATE, OUTPUT_RATE = 300, 1200  # peak USD0.30/1M and USD1.20/1M
CONTEXT, MAX_OUTPUT = 1_048_576, 65_536
RESERVE = CONTEXT * INPUT_RATE + MAX_OUTPUT * OUTPUT_RATE
KEY = Path.home()/'.config/agent-ecosystem/secrets/deepseek.key'
STATE = Path.home()/'.local/share/coordina/deepseek-official-budget.sqlite3'
# Verified 2026-11-08 UTC: https://api-docs.deepseek.com/quick_start/pricing/
# Internal recheck deadline only; neither resets CAP nor guarantees future prices.
PRICE_VALID_UNTIL = '2026-11-10T00:00:00+00:00'


@contextlib.contextmanager
def ledger():
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(str(STATE), timeout=8)
    try:
        os.chmod(STATE, 0o600)
        conn.execute('CREATE TABLE IF NOT EXISTS charges (id TEXT PRIMARY KEY, amount INTEGER NOT NULL, state TEXT NOT NULL, created REAL, usage TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS budget_policy (id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL CHECK(cap>=0))')
        conn.execute('INSERT OR IGNORE INTO budget_policy(id,cap) VALUES(1,?)', (CAP,))
        conn.execute('CREATE TABLE IF NOT EXISTS budget_events (id TEXT PRIMARY KEY, created REAL NOT NULL, old_cap INTEGER NOT NULL, new_cap INTEGER NOT NULL, used_at_change INTEGER NOT NULL, authorization_json TEXT NOT NULL)')
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def current_cap(conn):
    row=conn.execute('SELECT cap FROM budget_policy WHERE id=1').fetchone()
    if row is None or type(row[0]) is not int or row[0]<0:
        raise ValueError('Política de presupuesto inválida; no se habilita gasto.')
    return row[0]


def change_limit(new_cap, expected_cap, authorization, event_id):
    """Administrative, explicit authorization only; no CLI command or reset of charges."""
    if type(new_cap) is not int or new_cap<0 or type(expected_cap) is not int:
        raise ValueError('Tope inválido.')
    required=('human_quote','source','observed_at','evidence')
    if not event_id or not isinstance(authorization,dict) or any(not isinstance(authorization.get(k),str) or not authorization[k].strip() for k in required):
        raise ValueError('Falta constancia de autorización expresa.')
    with ledger() as c:
        existing=c.execute('SELECT new_cap FROM budget_events WHERE id=?',(event_id,)).fetchone()
        if existing:
            if existing[0]!=new_cap: raise ValueError('Evento existente con otro tope.')
            return {'event_id':event_id,'changed':False}
        old=current_cap(c)
        if old!=expected_cap: raise ValueError('El tope cambió; revisar antes de modificarlo.')
        used=c.execute('SELECT COALESCE(SUM(amount),0) FROM charges').fetchone()[0]
        if new_cap<used: raise ValueError('El tope no puede ser menor al gasto y reservas contabilizados.')
        c.execute('INSERT INTO budget_events VALUES(?,?,?,?,?,?)',
                  (event_id,time.time(),old,new_cap,used,json.dumps(authorization,ensure_ascii=False)))
        c.execute('UPDATE budget_policy SET cap=? WHERE id=1',(new_cap,))
    return {'event_id':event_id,'changed':True,'old_cap':old,'new_cap':new_cap,'used_at_change':used}


def status():
    with ledger() as c:
        cap=current_cap(c)
        used = c.execute('SELECT COALESCE(SUM(amount),0) FROM charges').fetchone()[0]
        pending = c.execute("SELECT COUNT(*) FROM charges WHERE state='reserved'").fetchone()[0]
    return dict(limit_usd=cap/1e9, accounted_upper_usd=used/1e9,
                remaining_usd=max(0,cap-used)/1e9, uncertain_or_active=pending,
                model=MODEL, price_valid_until=PRICE_VALID_UNTIL)


def reserve(jid):
    if dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(PRICE_VALID_UNTIL):
        raise ValueError('Verificar tarifas oficiales antes de continuar; presupuesto no renovado.')
    with ledger() as c:
        if c.execute('SELECT 1 FROM charges WHERE id=?',(jid,)).fetchone():
            raise ValueError('Solicitud ya contabilizada; no se repite la llamada.')
        used = c.execute('SELECT COALESCE(SUM(amount),0) FROM charges').fetchone()[0]
        if used+RESERVE > current_cap(c):
            raise ValueError('Tope acumulado autorizado: saldo insuficiente para reservar esta llamada.')
        c.execute('INSERT INTO charges VALUES (?,?,?,?,?)',(jid,RESERVE,'reserved',time.time(),None))


def settle(jid, usage):
    # Missing/malformed usage, cancellation, crash or timeout keep the full reserve.
    inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
    if type(inp) is not int or type(out) is not int or not 0 <= inp <= CONTEXT or not 0 <= out <= MAX_OUTPUT:
        raise ValueError('Uso incompleto o fuera de límites; se conserva la reserva máxima.')
    amount = inp*INPUT_RATE + out*OUTPUT_RATE  # ignores discounts: conservative upper bound
    with ledger() as c:
        row=c.execute('SELECT state FROM charges WHERE id=?',(jid,)).fetchone()
        if not row or row[0]!='reserved':
            raise ValueError('Reserva inexistente o ya liquidada.')
        c.execute('UPDATE charges SET amount=?,state=?,usage=? WHERE id=?',
                  (amount,'settled',json.dumps(usage),jid))
    return amount/1e9


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirección de API rechazada; credencial no reenviada.')


def request(path, payload=None, timeout=120):
    if KEY.is_symlink() or KEY.stat().st_mode & 0o077:
        raise ValueError('La clave debe ser un archivo privado con permisos 600.')
    key=KEY.read_text().strip()
    if not key.startswith('sk-'):
        raise ValueError('Falta la API key oficial.')
    data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    req=urllib.request.Request('https://api.deepseek.com'+path,data=data,
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    # No SDK retries, no custom endpoint, no automatic redirects.
    with urllib.request.build_opener(NoRedirect).open(req,timeout=timeout) as r:
        body=r.read(8*1024*1024+1)
        if len(body)>8*1024*1024: raise ValueError('Respuesta demasiado grande')
        return json.loads(body)


def generate(jid, prompt, timeout=120, thinking='enabled'):
    if not prompt.strip() or len(prompt.encode())>240000:
        raise ValueError('Entrada vacía o mayor de 240000 bytes; dividir el lote.')
    # Check credential availability before reserving; never print it.
    if not KEY.is_file(): raise ValueError('Falta la API key oficial.')
    if thinking not in ('enabled','disabled'): raise ValueError('Modo de razonamiento inválido')
    payload=dict(model=MODEL,messages=[{'role':'user','content':prompt}],
                 max_tokens=MAX_OUTPUT,stream=False,thinking={'type':thinking})
    if thinking=='enabled': payload['reasoning_effort']='low'
    reserve(jid)
    data=request('/chat/completions',payload,timeout=min(120,timeout))
    usage=data.get('usage',{})
    cost=settle(jid,usage)
    choice=data.get('choices',[{}])[0]
    answer=choice.get('message',{}).get('content') or ''
    if choice.get('finish_reason')!='stop' or not answer.strip():
        raise ValueError('Respuesta incompleta; consumo registrado, sin reintento automático.')
    return dict(answer=answer,metadata=dict(model=MODEL,returned_model=data.get('model'),
        usage=usage,cost_upper_usd=cost,budget=status(),tools_enabled=False,thinking=thinking))


def main():
    os.umask(0o077)
    if sys.argv[1:] == ['status']:
        print(json.dumps(status())); return
    if sys.argv[1:] == ['balance']:
        print(json.dumps(request('/user/balance'))); return
    try:
        result=generate(sys.argv[1],sys.stdin.read(),int(sys.argv[2]),sys.argv[3] if len(sys.argv)>3 else 'enabled')
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        # Errors may contain request data; only known local messages are exposed.
        message=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        print(json.dumps({'error':message}),file=sys.stderr)
        sys.exit(1)


if __name__=='__main__': main()
