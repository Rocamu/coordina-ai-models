# OpenCode restringido y exportación

`deepseek` (`opencode-deepseek`) usa `opencode/deepseek-v4.1-flash-free`. `glm` (`opencode-glm`) usa OpenCode GLM5.3FlashFree; `nemotron` (`nvidia-nemotron`) usa NVIDIA Nemotron3.5Lightning. `route=new` o auto normalizado, `rounds=1`, sin `session_id` ni `session_hint`. No hay fallback de pago; las cuotas no están garantizadas.

`consult` permite lectura/búsqueda; `edit` añade edición del workspace. No permite shell, MCP, plugins ni subagentes. Son políticas OpenCode, no sandbox del sistema operativo. Revisa `transcript[].metadata.tool_errors` y los metadatos de proveedor/modelo/coste antes de cerrar. DeepSeek aquí no identifica el chat de Harness.

## Guardado por el puente

`output_path` solo admite estos tres destinos. Archivo nuevo de ruta absoluta bajo `$COORDINA_OUTPUT_ROOT` (por omisión `~/Documents/Coordina/outputs/`), padre existente, sin symlinks ni sobrescritura. La raíz es frontera de permisos vigente. El puente escribe texto exacto UTF-8 y devuelve ruta, bytes y SHA256; consult no habilita edición del modelo por ello.

`output_check(output_path)` comprueba acceso sin modelos; se aplica antes de encolar, con límite de 8 s. Guardado: hasta 15 s. Si el plugin no accede a Documentos, no repitas: recupera `job_wait(compact=true)` sin output_path y guarda desde el entorno autorizado. Una VM solo ve sus montajes, no necesariamente registros del Mac.

Tras fallo de guardado, respuesta íntegra en `runs/JOB/1.answer.txt`. `job_export(job_id, output_path)` exporta a archivo nuevo sin cuota de modelo. Reutiliza request_id para recuperar el encargo; no generes otro para resolver un fallo de escritura. `compact=true` evita repetir encargo en status/wait/cancel.

Si la exportación falla otra vez, recuperar el texto disponible por el mismo job o archivo de respuesta y guardarlo desde el entorno autorizado, sin ampliar la raíz del puente. Si no se puede recuperar completo, informar el impedimento y conservar el encargo; no regenerar para resolver la escritura. Bytes y SHA256 del guardado acreditan la respuesta, no una edición solicitada.
