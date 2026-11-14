# Hilos nativos y ejecutores

Codex y Claude existentes usan el hilo identificado. En este Mac un hilo Codex sin receptor puede no estar cargado en la app; no concluir que cayó por su ausencia en conversations. Con ID o título único, el adaptador puede cargarlo mediante `codex://threads/ID`. Si falla discovery en MCP residente, prueba CLI actual para el mismo hilo. No atribuyas esta causa a todo fallo de receptor sin evidencia.

Codex y Claude independientes son efímeros: `--ephemeral` / `--no-session-persistence`. Sus IDs de telemetría no autorizan ni garantizan route=existing. Conserva contexto y checkpoints explícitos para trabajo posterior.

Antigravity es un adaptador del host; su hilo se descubre y alcanza mediante su integración. Una nueva sesión crea un destino de ese adaptador, no una ejecución cloud equivalente. Para transporte remoto lee transportes.md.

## Encargos largos Codex

Para límites de timeout, rondas y admisión usa la tabla normativa de [estados](estados.md). La ampliación de plazo solo corresponde a Codex, route=new y rounds=1; no cambia otras rutas ni transforma observación de un chat existente en orden de terminarlo.

Desde 2.3.11, `model` y `reasoning_effort` son opcionales por encargo, exclusivamente para Codex/Claude/Grok con `route=new` explícito y `rounds=1`. No se aceptan con auto/existing ni Gemini, y no cambian la configuración global. Omitirlos conserva el comportamiento previo. La compatibilidad de cada nivel depende del modelo; un rechazo del CLI se informa sin cambiar de modelo ni repetir inferencia.

| Destino | `reasoning_effort` admitido por el adaptador | Opción CLI |
| --- | --- | --- |
| Codex | low, medium, high, xhigh | `-c model_reasoning_effort=...` |
| Claude | low, medium, high, xhigh, max | `--effort` |
| Grok | none, minimal, low, medium, high, xhigh, max, deep | `--reasoning-effort` |

`model` es un único selector de hasta 200 caracteres, sin espacios ni opciones CLI. Usa el ID completo admitido por el proveedor y conserva el ID reportado; un nombre completo tampoco demuestra que sea un snapshot inmutable. `opus`, `sonnet`, `flash`, `default` y otros alias conocidos quedan señalados como móviles.

La transcripción registra `model_selection.requested` (petición explícita), `configured` (selección de respaldo al arrancar) y `sent_to_cli` (selector enviado). La selección se conserva también antes del arranque, incluso si luego vence el plazo. El campo histórico Codex `requested_model` sigue significando el selector enviado al CLI. `requested_reasoning_effort` no certifica el esfuerzo efectivo.

Se conservan los campos estructurados del CLI, cuando existen: `model`, `canonicalModel`, `modelUsage`, uso íntegro, costo y base del costo. Las afirmaciones en el texto del modelo no se usan como evidencia. Codex puede devolver tokens sin ID servido; ese ID queda ausente. Un costo de lista o reportado no acredita factura; un campo ausente no equivale a cero. No se extiende el presupuesto DeepSeek a otros proveedores.

Si el MCP residente no admite esas opciones, usa la CLI instalada sin reiniciar trabajos activos. Ejemplo de forma, no encargo para ejecutar: `python3 "~/Library/Application Support/Coordina/runtime/coordina.py" submit codex "ENCARGO" --workspace "/RUTA/PROYECTO" --route new --rounds 1 --mode edit --timeout 1800 --delivery-timeout 120 --model ID_COMPLETO --reasoning-effort medium`.

La cola de ejecutores independientes es única; los chats nativos usan otra ruta. Para lotes, definir manifiesto de pendientes y checkpoint por lote validado, con salida antes del plazo. Reanudar solo pendientes y preservar la separación de información en evaluaciones independientes. job_resume no reanuda una inferencia vencida. Probar un nivel de razonamiento en muestra separada antes de cambiar el criterio de una operación en curso.
