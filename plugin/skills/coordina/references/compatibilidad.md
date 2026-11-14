# Compatibilidad, capacidades y CLI

La CLI de este Mac es `~/Library/Application Support/Coordina/runtime/coordina.py`. No presupongas esa ruta en otro equipo. Invoca con Python y argumentos estructurados: `conversations DESTINO --workspace CARPETA`, `submit DESTINO ENCARGO --workspace CARPETA --route auto`, `wait ID --seconds 45`, `show ID`. Para un hilo: `--route existing --session-id ID`.

`status` y MCP `bridge_status` no gastan modelos. Desde runtime 2.3.10 exponen `capabilities`, `runtime_version`, `skill_revision` y `job_contract_version`, conservando el campo `version` anterior. El inventario incluye límites y ámbito de Codex, clases de seguimiento, progreso/final, reanudación CAS, idempotencia y guardado seguro. Describen el proceso que responde, no certifican disponibilidad de proveedores ni el esquema de otra conexión residente. Antes de una opción nueva, comprueba que la herramienta que vas a llamar la admite; si no, usa la CLI actual. Repetir comprobación tras reconexión, actualización o error de esquema, sin reiniciar trabajos activos.

`source_family` y `source_session_id` son contexto remoto opcional; no requieren receptor local del emisor. `source_required` o imposibilidad de verificar origen son fallos del contrato 2.2: usar conector actualizado, CLI en su host u otro transporte que llegue al mismo destino. No pedir al usuario registrar buzones.

Una conexión cargada puede mantener versión o esquema anterior hasta su reconexión normal. Actualizar archivos de skill no reescribe textos ya cargados en conversaciones. No declarar actualizada una conexión solo porque su archivo cambió.

Desde 2.3.11 las capacidades `per_job_model`, `executor_reasoning_effort` y `cli_model_metadata` describen los selectores por encargo y el alcance de sus metadatos; consulta [ejecutores](ejecutores.md). No requieren migrar trabajos históricos ni ampliar permisos. Las conexiones anteriores pueden seguir atendiendo encargos sin esos parámetros; para usarlos antes de reconectar, utiliza la CLI actual.

Desde 2.3.12 `independent_concurrency.max_workers=2` permite dos ejecutores independientes. Cada proceso nuevo carga el runtime instalado, incluso si lo lanzó un MCP anterior; su `bridge_status` puede seguir mostrando la versión cargada antes de la actualización. No es necesario cerrar conversaciones. La compatibilidad con el bloqueo exclusivo antiguo evita solaparse con ejecutores previos aún activos. El presupuesto de DeepSeek oficial/fast sigue siendo un único libro transaccional acumulado de US$10, sin reinicio ni multiplicación por plaza.

Desde 2.3.13 `freellm_model_selection` expone la lista gratuita NVIDIA permitida. `coordinate` acepta `model` para FreeLLM con `route=new`, una ronda, sin `reasoning_effort`. En un MCP anterior utiliza Ultra omitiendo `model`, o selecciona los demás mediante la CLI actual; consulta [FreeLLMAPI](freellm.md).

La fuente canónica es `plugin/skills/coordina/` en el repositorio fuente. `install.py` distribuye referencias y núcleo; las extensiones comunes del host viven en `local-foro.md`, las de Harness en `local-harness.md`. El manifiesto de instalación registra hashes y extensiones; no editar directamente caches de plugins.
