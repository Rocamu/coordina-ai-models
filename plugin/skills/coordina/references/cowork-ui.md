# Cowork por interfaz

Usa esta vía cuando el conector no permita enviar al chat elegido. Localiza el hilo y observa su URL `https://claude.ai/chat/ID`; una URL de proyecto no identifica un hilo.

Prepara con `coordinate(target="cowork", session_id=URL, session_hint=TITULO, ...)`. `ui_required` y `spec.route=ui` significan preparado, no enviado. El origen puede quedar desconocido y el puente genera un ID si no pasaste `request_id`.

Verifica la URL, pega `message`, envía y observa el mensaje en ese hilo. Registra `ui_receipt(job_id=id, state="submitted", observed_url=URL, observed_text=MENSAJE_COMPLETO)`. Ante resultado incierto registra `delivery_unknown`. No registres como enviado lo que solo está en el compositor.

Lee la respuesta con `COORDINA[id]`, registra `ui_receipt(..., state="completed", observed_text=RESPUESTA)` y tráela al solicitante. Estos recibos son observaciones del agente: el puente no vigila la interfaz. No detengas trabajo del receptor para entregar una consulta que puede esperar.
