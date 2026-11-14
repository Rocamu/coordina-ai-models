# FreeLLMAPI local: modelos gratuitos NVIDIA

`target="freellm"`, `route="new"`, `mode="consult"`, `rounds=1`, `timeout=120` utiliza FreeLLMAPI Desktop en `http://127.0.0.1:31415/v1`. Incluye toda la entrada en `task`; no hay herramientas ni `output_path`. Recupera `job_wait(compact=true)` y guarda desde tu entorno.

Desde 2.3.13 el parámetro opcional `model` permite seleccionar:

| Modelo | ID exacto |
| --- | --- |
| Nemotron Ultra, predeterminado | `nvidia/nemotron-3-ultra-550b-a55b` |
| Nemotron Lightning | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| GPT-OSS-20B | `openai/gpt-oss-20b` |

Las tres páginas de NVIDIA Build mostraban Free Endpoint Available el 8-nov-2026 UTC. Son rutas de prueba gratuitas con cuota y disponibilidad del proveedor; no se garantiza uso ilimitado ni calidad para un lote legal.

El adaptador exige que el modelo elegido esté habilitado, una sola clave NVIDIA activa cuyo alcance sea un subconjunto explícito de esta lista, y unificación activa. Envía `nvidia:<ID exacto>`: el servidor resuelve ese miembro concreto y limita su cadena a él. Las adiciones de otros modelos al catálogo no bloquean esta ruta ni se convierten en respaldo. Verifica además el modelo devuelto y `X-Routed-Via`; rechaza sustituciones. Mantiene el límite de un fallo upstream, sin fallback pagado. Los metadatos incluyen proveedor, modelo, HTTP, tiempo, uso y coste configurado cero.

Un MCP anterior puede usar inmediatamente `target="freellm"` sin `model` (Ultra). Si aún no expone `model`, usa la CLI actual desde el Mac, sin reiniciar Claude:

```sh
python3 "~/Library/Application Support/Coordina/runtime/coordina.py" submit freellm 'ENCARGO CON TEXTO COMPLETO' --workspace '/RUTA/PROYECTO' --route new --mode consult --rounds 1 --timeout 120 --model nvidia/nemotron-3.5-lightning-30b-a3b
```

El retorno es un `job_id`; consulta el mismo encargo con `job_wait`/`job_status` o la CLI. No regeneres ante fallos de entrega. Las rutas oficiales de DeepSeek conservan su libro separado de US$10 acumulados, sin reset. FreeLLM no llama a esa API ni modifica ese contador.
