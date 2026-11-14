# Estados, plazos y recuperación

El estado global es de compatibilidad. `delivery` describe entrega; `response`, respuesta; `execution`, ejecución declarada o comprobada. `contract_version=1` identifica este esquema de seguimiento, no la versión del runtime.

| Capa o campo | Valores/condición relevantes | Interpretación y siguiente acción |
| --- | --- | --- |
| delivery | `not_submitted` | En cola o preparado; no prueba envío |
| delivery | `accepted`, `submitted=true` | Destino aceptó; esperar mismo job sin reenviar |
| delivery | Incierta / global `delivery_unknown` | Pudo enviarse; revisar recibo/marcador sin repetir |
| delivery | `expired`, `submitted=false` | Venció admisión; consultar `resume_allowed` y error antes de actuar |
| response | `not_started`, `pending` | No llegó respuesta final; seguir mismo job |
| response | `kind=progress` | Avance; no cierra intercambio |
| response | `received`, `kind=final` | Respuesta final correlacionada; evaluar su contenido |
| execution | `not_verified` | No se comprobó ejecución |
| execution_ack | `started`, `blocked`, `finished` | Declaración del receptor con `verified=false`, no certificación |
| global | `completed` | Hubo respuesta; no implica ejecución exitosa |
| global | `ui_required` | Mensaje preparado para interfaz, aún no enviado |
| global | `session_busy`, `session_unavailable` | Consultar entrega y `resume_allowed`; el nombre solo no autoriza reenvío |
| global | `held`, `delivery_refused`, `cancelled` | No forzar reentrega ni tratar cancelación como retirada de mensaje |
| needs_attention / next_action | true / descripción | Diagnóstico al consultar; no existe aviso push al emisor |

Los valores no enumerados se interpretan a partir de sus tres capas y del error concreto; no se consideran seguros para reenvío por descarte.

## Plazos

| Parámetro y ruta | Valor y límites | Inicio y alcance |
| --- | --- | --- |
| `timeout`, CLI independiente | 300 s predeterminado; 15–900 s, salvo excepciones siguientes | Cada ronda; el seguimiento agregado usa aceptación + rounds × timeout |
| `timeout`, Codex/new/rounds=1 | 15–3600 s | Preparación y generación de la ronda |
| `timeout`, FreeLLMAPI | Hasta 120 s | Ejecutor API local; no generalizar el máximo de otros destinos |
| `delivery_timeout`, CLI independiente | 15–900 s; predeterminado `min(timeout,900)` | Desde creación hasta admisión local |
| `delivery_timeout`, hilo nativo/UI | 15–900 s; sin valor explícito usa timeout, limitado a 900 por esas rutas | Hilo nativo: desde inicio de entrega registrado por el observador; Cowork: desde creación hasta recibo de envío |
| `timeout`, hilo nativo | Hasta 900 s; ver opciones de la ruta | Respuesta desde aceptación, no desde búsqueda |
| `timeout`, Cowork UI | Hasta 900 s | Respuesta desde `ui_receipt(submitted)` |
| `job_wait` | Herramienta admite hasta 55 s; usar hasta 45 s por consulta | Observación; no renueva ni cancela trabajo |
| Limpieza de ejecutor restringido | Hasta 16 s adicionales | Parada cooperativa; no multiplicar reintentos |

No confundir tiempo de cola con generación. Una respuesta tardía correlacionada limpia el error de espera obsoleto; cancelar sigue cancelando el seguimiento. Las duraciones específicas de GPU/JOKER están en su referencia.

## Reanudación y progreso

Consultar estado y revisión inmediatamente antes de `job_resume(job_id, expected_revision)`. Solo `resume_allowed=true` habilita una decisión explícita para el mismo encargo busy/offline no enviado. Comparación atómica evita dos reanudaciones simultáneas. No reenvía accepted/unknown/held/refused ni cambia destino. Un nuevo encargo sobre pendientes tras una inferencia vencida debe distinguirse de esta operación y apoyarse en checkpoints.

CLI: `python3 "~/Library/Application Support/Coordina/runtime/coordina.py" resume ID --expected-revision REV`.

Para progreso: `reply ID CLAVE TEXTO --response-kind progress --execution-ack '{"state":"started","evidence":["evidencia concreta"]}'`. No publiques CLAVE. La respuesta final usa `final`.
