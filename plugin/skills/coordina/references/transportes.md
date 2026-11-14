# Transportes según el entorno

El lugar donde corre el agente y el lugar donde corre su herramienta pueden ser distintos. Comprueba qué alcanza la herramienta, no si el emisor aparece en los procesos del receptor.

| Situación | Vía útil | Retorno |
| --- | --- | --- |
| Origen local o cloud con acceso al MCP del Mac; destino local | coordinate, origen opcional | job_wait/job_status por ID |
| Hilo accesible por conector de la aplicación | Listar, leer y enviar al ID observado | Leer ese hilo o esperar con su herramienta |
| Destino de ejecución cloud | Conector/API oficial o modo cloud de su CLI | Estado y respuesta de esa sesión/tarea |
| Chat Claude/Cowork sin envío por conector | Interfaz autorizada, URL exacta | Respuesta con marcador en ese chat |
| Sin canal directo, con almacenamiento compartido | Encargo y respuesta con lector real confirmado | Acuse y resultado correlacionados |

## Origen cloud y destino local

No hace falta un proceso del emisor en el Mac. Si su MCP alcanza el puente, puede entregar y devolver el resultado por esa conexión. Conservar job_id permite reanudar tras un timeout o una reconexión. El ID cloud del emisor es contexto opcional, no una credencial.

Para un destino existente sí se necesita un medio real de alcanzarlo. Diagnostica sus fallos como problemas del transporte **al destino** y prueba otro conector sin cambiar de hilo.

## Destino cloud

Inspecciona las herramientas conectadas o el --help de la CLI instalada antes de enviar: sus opciones cambian. Usa la sesión cloud existente cuando se pidió esa. Para una continuación nueva cumple las condiciones de creación de la herramienta y del encargo.

Claude Code puede ofrecer ejecución cloud y control remoto; no son equivalentes. Codex/ChatGPT puede ofrecer tareas cloud por herramientas de aplicación o CLI. Una invocación local de claude o codex exec no acredita entrega a un chat cloud determinado.

Usa la autenticación ya disponible del proveedor. No copies tokens o perfiles entre entornos. Pasa texto suficiente, adjuntos autorizados o referencias legibles por el receptor. Un paquete con hashes puede ayudar con muchos archivos; no es requisito para una consulta breve.

## Retorno y continuidad

El solicitante recoge la respuesta por el ID del encargo, la sesión del proveedor o el canal compartido, y la integra en su conversación. No necesita que el destinatario descubra el hilo emisor por nombre.

Una consulta nube/local puede funcionar mientras el Mac está disponible. Continuar con el Mac apagado requiere un transporte en la nube: compruébalo cuando esa continuidad forme parte del encargo, sin convertirlo en requisito de toda coordinación.

Si no hay acceso visual al escritorio, sigue con las herramientas que no usan pantalla. Si solo la interfaz sirve y está inaccesible, informa esa limitación concreta sin confundirla con la inexistencia del hilo.

