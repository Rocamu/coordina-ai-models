# DeepSeek Harness y asignación local

Harness Desktop carga `~/.dsh/skills/coordina/SKILL.md`. La conexión stdio mcp-coordina del perfil desktop usa el mismo runtime con --client harness; las herramientas pueden llevar prefijo mcp__coordina__. La skill se lee a demanda y el MCP ejecuta: actualizar uno no garantiza que el otro recargue. No existe target=harness. deepseek es OpenCode gratuito; deepseek-official-fast es API con presupuesto; ninguno controla el chat abierto de Harness.

## Selección por coste y calidad (P-005)

Antes de elegir una ruta alternativa, consultar `~/.local/share/coordina/destinos.json`, operado por `~/.dsh/foro-agentes/destinos.py`. No sustituir el destino elegido por el usuario con un ranking. Elegir entre rutas admitidas la más barata que cumpla calidad, plazo y verificación, incluyendo reintentos. Coste desconocido no es cero: ranking sitúa null al final; solo elegirlo cuando ninguna ruta conocida cumpla, registrando sin ranking por coste. Sin fila, una exploración; ninguna ruta pagada sin autorización y tope aplicable al encargo y a sus reintentos. La cuarentena no habilita cambiar proveedor exigido ni ampliar presupuesto.

Para consult sensible a calidad, ranking --min-calidad 3.5; el ranking sin filtro solo compara coste/tiempo. net_s excluye wait_ms; wait_max_s puede descalificar aunque mediana_s sea baja. Tres null/429 consecutivos en 24 h abren cuarentena; re-admitir a las 24 h con motivo y fecha, no declarar inaptitud permanente.

Desde 2.3.12 hay dos plazas globales para ejecutores independientes, incluidos los adaptadores API. Con presión de memoria de macOS en advertencia (2) solo se admiten cargas ligeras, con vigilancia de RSS de cada ejecutor y sus descendientes; no iniciar modelos locales pesados. Los observadores nativos quedan fuera de estas plazas. admission.json guarda wait_ms, slot y max_workers. La espera la rige delivery_timeout según estados.md. Un ejecutor anterior que aún retenga el bloqueo exclusivo debe finalizar antes de admitir los nuevos; no se interrumpe por actualizar. Las ediciones de carpetas solapadas siguen protegidas por reservas.

Para los encargos del foro registrar tipo, ruta, resultado, modelo y coste reportado o no reportado en RESUELTO. En edit incluir execution_ack con evidencia concreta; la ficha local incluye job_id/request_id, delivery/response/execution, modelo real y coste. Su falta es avance para ese flujo de foro, no cambio del esquema global del puente. El ack sigue siendo declaración no verificada. Verificador rotativo semanal sin rotar liderazgo.
