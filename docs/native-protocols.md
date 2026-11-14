# Comunicación con conversaciones existentes

Coordina distingue una sesión guardada de una conversación que tiene un receptor conectado. Un título en el historial no prueba que la aplicación esté preparada para recibir un encargo. La selección combina metadatos del escritorio con la comprobación del receptor antes de enviar.

## Codex local

El escritorio de este Mac expone una conexión Unix privada en `~/.codex/ipc/ipc.sock`. El cliente se identifica y consulta el propietario de una conversación mediante `thread-owner-discovery`. Un propietario debe anunciar que admite contenido de otras aplicaciones como entrada no confiable. La entrega utiliza `thread-follower-start-turn` al propietario verificado, conservando la configuración del hilo.

El encargo se presenta como resultado de una herramienta de entrada no confiable, no como una instrucción de sistema ni como consentimiento humano. Si la conversación tiene un turno en curso, el escritorio rechaza esa entrega antes de incorporar el contenido. Coordina espera a que quede disponible. Un resultado perdido o desconocido impide el reenvío automático.

El inventario utiliza los identificadores de actividad del escritorio y metadatos de conversaciones guardadas. Cada candidato se comprueba por la conexión nativa. Una conversación nombrada explícitamente también se verifica directamente. Los títulos visibles proceden del nombre guardado; los archivos de la aplicación se abren solo para lectura. La respuesta se identifica por el turno confirmado y sus elementos finales, o se devuelve mediante la clave de retorno del encargo.

Este transporte del escritorio es distinto del protocolo público de `codex app-server`. Es una interfaz interna y puede cambiar entre versiones. Los métodos, versiones y el control de turno ocupado se comprobaron contra el paquete instalado de ChatGPT/Codex el 16 de octubre de 2026. Ante una forma desconocida de inventario, propietario, protocolo o recibo, el puente informa el impedimento y no abre otra conversación como sustitución.

## Claude Code local y su aplicación de escritorio

Claude Code publica las sesiones conectadas en su registro local y ofrece mensajería nativa entre sesiones. Coordina valida el propietario de los metadatos, la identidad y fecha de inicio del proceso, la versión del protocolo y el socket receptor. La entrega usa la capacidad de par publicada para ese receptor; no lee ni suplanta el token reservado a sus procesos hijos.

El receptor conserva sus controles de admisión. Una recepción retenida o rechazada se comunica como tal. El mensaje contiene un identificador de encargo; una respuesta final que lo incluye puede recuperarse del registro de esa conversación a partir del punto de envío, sin leer su historial completo. La herramienta `coordina_reply` ofrece otra vía correlacionada y permite recuperar una respuesta que llegue después de la espera inicial. Esa devolución guarda el resultado del encargo; no inicia un turno en otra conversación.

### Clase de permisos del remitente Codex

En Claude Code 2.1.304, el mensaje ordinario extrae la clase del remitente del sobre textual nativo `cross-session-message`, atributo `from-mode`; el campo JSON `from_mode` corresponde a avisos de control y no identifica por sí solo un mensaje ordinario. El sobre conserva el origen como mensaje de otro agente y la identidad de la sesión Codex. No convierte el contenido en consentimiento humano.

El puente consulta en modo de solo lectura el registro de la sesión Codex identificada por `CODEX_THREAD_ID` o `CODEX_SESSION_ID`. Solo declara `bypass` cuando constan simultáneamente `approval_mode=never` y sandbox explícitamente deshabilitado. Las sesiones restringidas sin preguntas conservan la clase de `dontAsk` (`prompting`). Un registro desconocido, incompleto o archivado no permite declarar una clase. El proceso observador vuelve a comprobar el registro antes de enviar. Las reglas del receptor, incluida una retención explícita, siguen aplicándose.

Un MCP compartido que no herede la identidad de la conversación conserva el origen desconocido. Para conservar la identidad verificable de la conversación, la CLI puede ejecutarse desde su terminal autorizado, que hereda esos identificadores. No se inventan identificadores ni se establece una clase mediante una opción elegida por el modelo.

Comprobación del 20 de octubre de 2026: 48 pruebas del puente, incluidos casos de permisos desconocidos, sesiones archivadas, sandbox restringido y serialización del sobre. El formato se cotejó con la versión instalada y con la [documentación de controles de recepción](https://code.claude.com/docs/en/cross-session-messaging#control-inbound-messages).

Solo se consideran sesiones cuyo receptor y proceso sigan vivos. Los chats de otras superficies que no publiquen un receptor local requieren una integración diferente. La documentación de Claude distingue sus sesiones locales, las sesiones remotas y los controles de recepción; el puente no altera esos límites.

## Antigravity

La aplicación Antigravity de Google (versión 2.24.0, comprobada el 17 de octubre de 2026) no instala un ejecutable en el `PATH`. Su servidor de lenguaje, `Contents/Resources/bin/language_server`, atiende en `127.0.0.1` un puerto HTTPS y otro sin cifrar; el segundo admite JSON del protocolo Connect y gRPC. Cada llamada exige la cabecera `x-codeium-csrf-token` con el token que la aplicación entrega a su servidor como argumento `--csrf_token`. Coordina lee ese token solo de procesos del mismo usuario cuyo ejecutable pertenece al paquete de Antigravity y no es modificable por otros usuarios. La conexión no usa los proxies configurados ni sale de la máquina.

El inventario usa `LanguageServerService/GetAllCascadeTrajectories`: conversaciones cargadas, título, carpetas, proyecto, estado (`CASCADE_RUN_STATUS_IDLE` o `RUNNING`) y número de pasos. Las trayectorias de subagentes se excluyen. Si la aplicación está cerrada o no responde, el resultado es `discovery_unavailable`.

La entrega usa la CLI de agente que Antigravity publica para sus sidecars: `language_server agentapi send-message --title=Coordina <conversación> <texto>`, con `ANTIGRAVITY_LS_ADDRESS` y `ANTIGRAVITY_CSRF_TOKEN`. El texto llega como mensaje de agente (`CORTEX_STEP_TYPE_SYSTEM_MESSAGE`, «[Message] sender=system»), no como entrada del usuario, e inicia un turno. Coordina solo entrega cuando la conversación está inactiva. Una entrega sin recibo pasa a `delivery_unknown` y no se repite.

La respuesta se lee con `GetCascadeTrajectorySteps` desde el número de pasos anterior al envío (`stepOffset`). Se localiza el mensaje que contiene `COORDINA[id]` y se toma la última respuesta del modelo (`CORTEX_STEP_TYPE_PLANNER_RESPONSE`) anterior a la siguiente entrada, cuando la conversación vuelve a estar inactiva. Una entrada posterior del usuario u otro agente delimita el turno; su respuesta no se atribuye al encargo. Un paso en `CORTEX_STEP_STATUS_WAITING` indica una aprobación pendiente en la interfaz de Antigravity: Coordina la comunica y no la concede.

La ruta nueva crea una conversación visible con `agentapi new-conversation --model=flash --title=… -- <texto>` en el proyecto de Antigravity que contiene la carpeta, o en «Outside of Project». El proyecto conserva sus políticas de archivos, comandos y revisión.

Coordina también se instala como plugin de Antigravity en `~/.gemini/config/plugins/coordina` (`plugin.json`, `mcp_config.json` y la skill). El servidor de lenguaje lo descubre sin reiniciar, inicia el MCP `coordina_coordina` y ofrece `coordina_reply`, `coordinate` y las demás herramientas. Cada llamada a una herramienta MCP queda sujeta a la aprobación que configure el usuario en Antigravity.

`agentapi` es la interfaz documentada para sidecars. `GetAllCascadeTrajectories` y `GetCascadeTrajectorySteps` son métodos internos y pueden cambiar entre versiones; ante una forma desconocida, el puente informa el fallo y no abre otra conversación como sustitución.

## Selección y recuperación

La primera solicitud propone destinos por carpeta y tema. Una elección explícita vincula únicamente la conversación solicitante, la familia de destino y el proyecto. Si la misma conversación solicita otra colaboración dentro de ese proyecto y el receptor sigue conectado, se reutiliza la elección. Otra conversación de origen conserva su propia selección.

No hay registro manual de buzones. La cola de encargos y sus claves de retorno pertenecen al puente y se crean automáticamente. La falta de un receptor adecuado puede justificar una sesión nueva; un fallo de descubrimiento, una conversación ocupada o un recibo incierto no prueban esa falta.

## Referencias

- [Mensajería entre sesiones de Claude Code, incluidos sockets locales y controles de recepción](https://code.claude.com/docs/en/cross-session-messaging).
- [Protocolo público de Codex app-server](https://learn.chatgpt.com/docs/app-server).
- [Implementación abierta de un puente con los transportes de escritorio](https://github.com/WebisityStudio/claude-codex-mcp-bridge), usada como referencia del formato local, con licencia MIT.
