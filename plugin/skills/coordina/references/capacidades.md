# Capacidades y permisos por destino

Matriz de controles observados en código del host. No certifica conexión, cuota, autenticación ni aislamiento efectivo de otro cliente. Los permisos de un hilo existente pertenecen a ese hilo. Una herramienta disponible no autoriza usarla fuera del encargo.

| Destino/ruta | Lectura y escritura | Shell, red y MCP | Gasto y persistencia |
| --- | --- | --- | --- |
| Codex nuevo consult/edit | Sandbox CLI read-only/workspace-write | CLI puede usar shell/red según sandbox y configuración; apps/plugins deshabilitados; excepción foro si instalado | Modelo configurado, coste no presumido cero; ephemeral |
| Claude nuevo consult/edit | Read/Glob/Grep; edit agrega Edit/Write según motor de permisos, no sandbox OS | Sin shell ni navegador ni subagentes por tools; MCP estricto con excepción foro | Cuenta/modelo configurado; sin persistencia de sesión |
| DeepSeek/GLM/Nemotron nuevo | consult lectura/búsqueda; edit añade herramientas de edición de workspace | Políticas OpenCode, no shell/MCP/plugins/subagentes; no presumir sandbox OS o red bloqueada | Sin fallback pagado; cuota proveedor; una ronda, retorno job_id |
| DeepSeek oficial/fast | Entrada textual task; sin herramientas del modelo | Adaptador HTTP, no lectura de archivos del usuario por modelo | Libro acumulado US$10, vigencia desde adaptador; una ronda sin sesión persistente |
| FreeLLMAPI | Entrada task; sin herramientas del modelo | HTTP local y upstream por servidor | Lista NVIDIA cerrada, coste configurado cero, cuota limitada; una ronda |
| Grok nuevo, en host del puente ([selección de equipo](grok.md)) | read-only o perfil edit coordina-grok; alcance edit incluye otras rutas autorizadas por perfil | CLI y perfil; controles efectivos de shell/red no verificados aquí | Modelo y coste reportados; ID de CLI no acredita chat nativo |
| Muse | consult textual; no edición por esta interfaz | Adaptador API; sin herramientas de modelo verificadas | Tope/credencial del ejecutable; funcionamiento no validado aquí |
| Codex/Claude existente, Cowork, Antigravity, JOKER | Permisos del receptor; consult/edit expresa alcance solicitado | Capacidades de aplicación/servicio; no verificadas por el puente por defecto | Sesión destino existente/nueva según adaptador; JOKER puede consumir GPU |
| Meta AI | coordinate rechazado | Deshabilitado como destino | No invocar como ejecutor |

`output_path` es escritura del puente, independiente de herramientas del modelo: solo DeepSeek/GLM/Nemotron y raíz fija de su referencia. Edit no autoriza ampliar esa raíz.

La subdelegación depende de autorización y controles reales: worker impide crear coordinaciones recursivas mediante su ruta; OpenCode restringe subagentes; Claude restringe tools. No generalizar esos controles a cualquier transporte ni convertir una mera instrucción en garantía técnica.
