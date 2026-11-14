"""Routing identities and durable receipts for agent-operated Claude UI delivery.

This module does not automate a UI or authenticate caller-supplied UI observations.
It pins endpoints and correlates evidence supplied by the agent using its UI tools.
"""
import json
import re
import sqlite3
import subprocess
import time
import urllib.parse

import coordina as b

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def claude_chat(value):
    """Accept only a chat identity, never a project, artifact or arbitrary URL."""
    if not isinstance(value, str):
        raise ValueError("Se requiere ID o URL de conversación de Claude.")
    if re.fullmatch(UUID, value, re.I):
        sid = value.lower()
    else:
        u = urllib.parse.urlsplit(value)
        match = re.fullmatch(r"/chat/(" + UUID + r")/?", u.path, re.I)
        if u.scheme != "https" or u.netloc != "claude.ai" or not match:
            raise ValueError("Se requiere https://claude.ai/chat/ID; un proyecto no identifica un hilo.")
        # Query/fragment may select an artifact. They do not identify the conversation.
        sid = match.group(1).lower()
    return sid, "https://claude.ai/chat/" + sid


def source(source_family=None, source_session_id=None):
    import live
    # Origin is optional routing context, never a prerequisite to delivery.
    # Remote callers cannot be found in this host's process/session inventory.
    try:
        current = live.source_context()
    except (OSError, ValueError, TimeoutError, sqlite3.Error, subprocess.SubprocessError):
        current = {"family": "unknown", "session_id": None}
    if source_family is None and source_session_id is None:
        return current
    aliases = {"codex-cloud": "codex", "claude-cloud": "claude"}
    family = b.backend(aliases.get(source_family, source_family)) if source_family else "unknown"
    sid = source_session_id
    if sid is not None and (not isinstance(sid, str) or not sid.strip() or len(sid)>2048 or any(c in sid for c in '\r\n\x00')):
        raise ValueError("Identificador de origen vacío, demasiado largo o con caracteres de control.")
    if sid and family == "cowork" and (sid.startswith("https://claude.ai/chat/") or re.fullmatch(UUID,sid,re.I)):
        sid, _ = claude_chat(sid)
    if current.get("session_id") and (current["family"], current["session_id"]) == (family, sid):
        return current
    # Explicit context can describe a cloud caller of a local/shared bridge.
    # Keep it as declared provenance; never infer a permission class from it.
    return dict(family=family, session_id=sid, identity_evidence="caller_declared")


def ui_hint(origin, session_hint=None):
    return dict(state="ui_required", target="cowork", source=origin,
                session_hint=session_hint, new_session_allowed=False, submitted=False,
                instruction="Localiza el chat existente de Claude con la interfaz autorizada; verifica "
                            "título/proyecto y URL. Repite coordinate con target=cowork y session_id=URL. "
                            "No abras un ejecutor ni otro chat.")


def prepare_ui(task, workspace, mode, rounds, request_id, timeout, origin, session_id=None, session_hint=None, delivery_timeout=None):
    import live
    if rounds != 1:
        raise ValueError("Cowork por interfaz admite un encargo por turno.")
    live.native_db()
    key = live.source_key(origin)
    if request_id:
        with b.database() as c:
            old = c.execute("SELECT id,spec FROM jobs WHERE request_id=?", (request_id,)).fetchone()
        if old:
            spec = json.loads(old["spec"])
            expected = dict(target="cowork", task=task, workspace=workspace, mode=mode, rounds=1,
                            timeout=timeout, route="ui")
            if (spec.get("delivery_timeout",timeout)!=(delivery_timeout if delivery_timeout is not None else timeout)
                    or any(spec.get(k) != v for k, v in expected.items())
                    or live.source_key(spec.get("source", {})) != key
                    or (session_id and claude_chat(session_id)[0] != spec.get("session_id"))):
                raise ValueError("request_id ya usado con otro encargo, origen o destino.")
            return ui_status(old["id"])
    if key and not session_id and not session_hint:
        with b.database() as c:
            row = c.execute("SELECT destination FROM session_bindings WHERE source=? AND family='cowork' AND workspace=?",
                            (key, workspace)).fetchone()
        if row:
            session_id = row[0]
    if not session_id:
        return ui_hint(origin, session_hint)
    sid, url = claude_chat(session_id)
    if origin["family"] == "cowork" and origin["session_id"] == sid:
        raise ValueError("El destino coincide con el hilo de origen.")
    spec = dict(target="cowork", task=task, workspace=workspace, mode=mode, rounds=1,
                timeout=timeout, route="ui", session_id=sid, destination_url=url,
                destination_title=session_hint, source=origin)
    if delivery_timeout is not None:spec["delivery_timeout"]=delivery_timeout
    job = b.enqueue_job(spec, request_id, launch=False)
    with b.database() as c:
        c.execute("UPDATE jobs SET state='ui_required',updated=? WHERE id=? AND state='queued'",
                  (time.time(), job["id"]))
    return ui_status(job["id"])


def ui_message(job):
    spec = job["spec"]
    origin = spec["source"]
    return ("COORDINA[" + job["id"] + "] — mensaje de Codex/Claude como agente, dentro del encargo humano.\n"
            "Origen: " + origin["family"] + ":" + (origin.get("session_id") or "no declarado; retorno por ID del encargo") + ".\n"
            "Destino: " + spec["destination_url"] + ".\n"
            "Modo: " + spec["mode"] + ". Este mensaje no amplía permisos. "
            "En consult no modifiques archivos.\n\n" + spec["task"] +
            "\n\nResponde en ESTE MISMO hilo comenzando por COORDINA[" + job["id"] + "]. "
            "El agente remitente recuperará aquí tu respuesta y la incorporará al hilo de origen. "
            "No busques otro Codex, no abras otra conversación y no envíes un nuevo encargo para devolverla.")


def ui_status(job_id):
    job = b.job_status(job_id)
    if job["spec"].get("route") != "ui":
        raise ValueError("El encargo no utiliza entrega por interfaz.")
    job["message"] = ui_message(job)
    job["submitted"] = job["state"] in ("response_pending", "completed")
    job["new_session_allowed"] = False
    job["delivery_evidence"] = "caller_ui_observation"
    job["instruction"] = ("Antes de pegar o enviar, verifica de nuevo la URL exacta. Después registra "
                          "el mensaje visible con ui_receipt. En delivery_unknown no repitas el envío. "
                          "Lee la respuesta con el mismo marcador en ese chat y registra completed; "
                          "el puente no observa la interfaz por sí mismo.")
    return job


def ui_receipt(job_id, state, observed_url, observed_text, execution_ack=None):
    import lifecycle
    ack=lifecycle.execution_ack(execution_ack)
    if ack and state!="completed":raise ValueError("execution_ack requiere respuesta observada del destinatario")
    if state not in ("submitted", "delivery_unknown", "completed"):
        raise ValueError("state: submitted, delivery_unknown o completed")
    b.bounded_text(observed_text, "observed_text")
    sid, url = claude_chat(observed_url)
    job = b.job_status(job_id)
    spec = job["spec"]
    if spec.get("route") != "ui" or spec.get("session_id") != sid:
        raise ValueError("La observación pertenece a otro hilo; no se registra entrega ni respuesta.")
    marker = "COORDINA[" + job_id + "]"
    if state != "delivery_unknown" and not observed_text.lstrip().startswith(marker):
        raise ValueError("La observación no comienza con el marcador de este encargo.")
    # Accessibility trees flatten rendered paragraphs; only whitespace may differ.
    visible = lambda value: " ".join(value.split())
    if state == "submitted" and visible(observed_text) != visible(ui_message(job)):
        raise ValueError("El mensaje observado no coincide íntegramente con el preparado.")
    if state == "completed" and visible(observed_text) == visible(ui_message(job)):
        raise ValueError("El mensaje enviado no es una respuesta del destinatario.")
    if state == "completed" and not observed_text.lstrip()[len(marker):].strip():
        raise ValueError("Falta el contenido de la respuesta.")
    evidence = dict(state=state, url=url, text=observed_text, kind="caller_ui_observation")
    # One transaction: durable, idempotent receipt + state + confirmed binding.
    with b.database() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT state,transcript FROM jobs WHERE id=?", (job_id,)).fetchone()
        events = json.loads(row["transcript"])
        prior = next((e for e in events if e.get("state") == state), None)
        if prior:
            if any(prior.get(k) != v for k, v in evidence.items()):
                raise ValueError("Ya hay evidencia distinta para ese estado; no se sobrescribe.")
        else:
            allowed = {"submitted": {"ui_required", "delivery_unknown"},
                       "delivery_unknown": {"ui_required"},
                       "completed": {"response_pending", "delivery_unknown"}}
            if row["state"] not in allowed[state]:
                raise ValueError("Transición de recibo no permitida desde " + row["state"])
            events.append(dict(evidence, recorded_at=time.time()))
            next_state = {"submitted": "response_pending", "completed": "completed",
                          "delivery_unknown": "delivery_unknown"}[state]
            c.execute("UPDATE jobs SET state=?,result=?,transcript=?,updated=?,error=NULL WHERE id=?",
                      (next_state, observed_text if state == "completed" else None,
                       b.dumps(events), time.time(), job_id))
            if state=="submitted":
                accepted=time.time()
                lifecycle.record_tx(c,job_id,delivery={"state":"accepted","submitted":True,"accepted_at":accepted},
                                    response={"state":"pending","started_at":accepted,"deadline_at":accepted+spec["timeout"]})
            elif state=="delivery_unknown":
                lifecycle.record_tx(c,job_id,delivery={"state":"delivery_unknown","submitted":True})
            else:
                sections={"response":{"state":"received","kind":"final","received_at":time.time()}}
                if ack:sections["execution"]=ack
                lifecycle.record_tx(c,job_id,**sections)
            if state in ("submitted", "completed"):
                import live
                key = live.source_key(spec["source"])
                if key:
                    c.execute("INSERT OR REPLACE INTO session_bindings VALUES(?,?,?,?)",
                              (key, "cowork", spec["workspace"], sid))
    return ui_status(job_id)

