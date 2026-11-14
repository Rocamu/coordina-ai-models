"""Additive exchange contract. Text is evidence, never executable instructions."""
import json
import time


def read(c, jid):
    row = c.execute("SELECT data FROM job_contract WHERE job_id=?", (jid,)).fetchone()
    return json.loads(row[0]) if row else {"revision": 0}


def record_tx(c, jid, **sections):
    d = read(c, jid)
    for name, fields in sections.items():
        d.setdefault(name, {}).update(fields)
    d["revision"] += 1
    c.execute("INSERT OR REPLACE INTO job_contract(job_id,data) VALUES(?,?)",
              (jid, json.dumps(d, ensure_ascii=False)))
    return d


def record(jid, **sections):
    import coordina as b
    with b.database() as c:
        c.execute("BEGIN IMMEDIATE")
        return record_tx(c, jid, **sections)


def execution_ack(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"state", "evidence"}:
        raise ValueError("execution_ack requiere state y evidence; no se interpreta texto libre")
    if value["state"] not in ("started", "blocked", "finished"):
        raise ValueError("execution_ack.state: started, blocked o finished")
    evidence = value["evidence"]
    if (not isinstance(evidence, list) or not 1 <= len(evidence) <= 8
            or any(not isinstance(x, str) or not x.strip() or len(x) > 2000 for x in evidence)):
        raise ValueError("execution_ack.evidence requiere 1 a 8 referencias no vacías")
    return dict(state=value["state"], evidence=evidence, verified=False,
                evidence_kind="recipient_reported", reported_at=time.time())


def project(job, contract, receipt=None):
    """No natural-language parsing, delivery retries, or side effects here."""
    state, route = job["state"], job["spec"].get("route")
    receipt = receipt or {}
    delivered = receipt.get("submitted") is True or state in ("awaiting_response", "response_pending", "completed")
    delivery = dict(state="accepted" if delivered else "not_submitted", submitted=delivered)
    if state in ("delivery_unknown", "delivery_refused", "held"):
        delivery["state"] = state
    delivery.update(contract.get("delivery", {}))
    response = dict(state="received" if state == "completed" else "pending" if delivered else "not_started")
    response.update(contract.get("response", {}))
    # Backward-compatible workers may settle an exchange without the new fields.
    if state == "completed":
        response.update(state="received", kind="final")
    if response.get("state") == "pending" and response.get("deadline_at", float("inf")) <= time.time():
        response["state"] = "timed_out"
    execution = dict(state="not_verified", verified=False, evidence=[])
    execution.update(contract.get("execution", {}))
    eligible = (route == "existing" and state in ("session_busy", "session_unavailable")
                and job.get("pid") is None and not job.get("cancel_requested")
                and receipt.get("state") in ("busy", "offline")
                and receipt.get("submitted") is False and not job.get("result")
                and not delivery.get("accepted_at") and delivery.get("submitted") is False)
    needs = state in ("failed", "interrupted", "blocked_memory", "session_busy", "session_unavailable",
                      "delivery_unknown", "delivery_refused", "held") or response["state"] == "timed_out"
    if state=="response_pending" and "deadline_at" not in response:needs=True
    if state=="ui_required" and time.time()>=job["created"]+job["spec"].get("delivery_timeout",job["spec"]["timeout"]):
        delivery["state"]="expired"
        needs=True
    if eligible:
        action = "job_resume con job_id y expected_revision; mismo destino, solo por decisión explícita"
    elif state in ("delivery_unknown", "held", "delivery_refused"):
        action = "Revisar el recibo en el destino fijado; no reenviar ni cambiar de chat"
    elif response["state"] == "timed_out" or state == "response_pending":
        action = "Recuperar respuesta del mismo encargo; no volver a entregar el mensaje"
    elif state == "completed":
        action = "Evaluar execution y su evidencia; completed solo acredita intercambio respondido"
    elif state == "ui_required" and needs:
        action = "Venció la preparación sin entrega observada; verificar el chat antes de cualquier envío manual"
    elif needs:
        action = "Revisar error y evidencia del mismo encargo; no reiniciar automáticamente"
    elif state == "ui_required":
        action = "Entregar por la interfaz al chat fijado y registrar ui_receipt"
    else:
        action = "Consultar job_wait o job_status; sin nueva entrega"
    job.update(contract_version=1, revision=contract.get("revision", 0), delivery=delivery,
               response=response, execution=execution, needs_attention=needs,
               next_action=action, resume_allowed=eligible, notification_channel="tool_response_polling")
    return job


def resume(jid, expected_revision, launch=True):
    """CAS at the durable boundary. Never re-send uncertain or accepted input."""
    import coordina as b
    import live
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision debe ser un entero no negativo")
    live.native_db()
    applied = False
    with b.database() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise ValueError("Encargo desconocido")
        d = dict(row); d["spec"] = json.loads(d["spec"])
        x = c.execute("SELECT receipt,reply_text FROM native_exchanges WHERE job_id=?", (jid,)).fetchone()
        contract = read(c, jid)
        previous = contract.get("delivery", {}).get("resume_from_revision")
        if previous != expected_revision:
            receipt = json.loads(x["receipt"] or "{}") if x else {}
            eligible = project(d, contract, receipt)["resume_allowed"]
            if (not eligible or not x or x["reply_text"] is not None
                    or contract.get("revision", 0) != expected_revision):
                raise ValueError("No reanudable: requiere busy/offline sin envío, PID cerrado y revisión vigente")
            marks = ','.join('?' for _ in b.TERMINAL)
            if c.execute("SELECT count(*) FROM jobs WHERE state NOT IN ("+marks+")", tuple(b.TERMINAL)).fetchone()[0] >= 8:
                raise ValueError("La cola ya contiene ocho trabajos activos")
            changed = c.execute("UPDATE jobs SET state='queued',error=NULL,updated=? WHERE id=? AND state=? AND pid IS NULL AND cancel_requested=0",
                                (time.time(), jid, row["state"])).rowcount
            if changed != 1:
                raise ValueError("El encargo cambió durante la reanudación")
            record_tx(c, jid, delivery={"state": "not_submitted", "submitted": False,
                "resume_from_revision": expected_revision, "attempt": contract.get("delivery", {}).get("attempt", 1)+1},
                response={"state": "not_started"})
            applied = True
    if applied and launch:
        live.launch_observer(jid)
    result = b.job_status(jid)
    result["resume_applied"] = applied
    return result
