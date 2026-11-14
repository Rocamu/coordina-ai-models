# Grok: este Mac y segundo equipo

El destino `grok` (alias `xai`) ejecuta Grok Build en el host del puente que recibe el encargo. Son dos destinos físicos distintos: Grok de este Mac y Grok del segundo equipo. El nombre del modelo no identifica el equipo.

## Seleccionar equipo

- **Este Mac:** usar el puente local con `target="grok"`, `route="new"`, `rounds=1`. En este host el ejecutable configurado es `~/.local/bin/grok` (configurable en `config.json`).
- **segundo equipo:** usar un puente o transporte confirmado que ejecute en el segundo equipo y su Grok instalado. Comprobar nombre del host, ejecutable, autenticación existente y carpeta remota. Un workspace local no implica que esa misma ruta exista en el segundo equipo. Transferir solo los materiales autorizados necesarios.
- Si el usuario exige uno de los equipos, conservar ese destino. No sustituir segundo equipo por este Mac ni viceversa al fallar conexión. Si el equipo es ambiguo, aclararlo antes de ejecutar; las tareas independientes sin preferencia pueden usar este Mac, indicándolo.
- El adaptador local actual no admite un parámetro `host`, ni destinos `grok-m1` o `grok-local`. No inventarlos. Sin conexión al segundo equipo comprobada, informar el dato de conexión que falta; una mención documental no acredita conectividad.

## Encargos y resultados

Admite consult/edit. auto se normaliza a new; existing, session_id y session_hint se rechazan. Un ID de sesión reportado por la CLI no acredita una conversación nativa continuable por esta ruta. No admite output_path.

`model` y `reasoning_effort` se pueden seleccionar por encargo con route=new y una ronda. Omitirlos conserva el modelo configurado. Los esfuerzos admitidos por el adaptador son none/minimal/low/medium/high/xhigh/max/deep; la CLI o el modelo pueden rechazarlos. Consultar [ejecutores](ejecutores.md) y la tabla de [plazos](estados.md).

consult usa el perfil read-only; edit usa coordina-grok. Verificar el perfil del equipo elegido: puede permitir workspace, foro y estado del puente. No presumir escritura exclusivamente dentro del workspace ni ausencia de shell/red. La disponibilidad de herramientas no amplía el encargo.

La respuesta JSON es `text`; conservar modelo seleccionado y reportado, host observado y metadatos disponibles de uso/coste. La versión CLI no acredita la versión del modelo. No presumir gratuidad ni alternar a otra ruta pagada para resolver un error.

Conservar job_id y request_id; recuperar el mismo job ante respuesta pendiente o entrega incierta. `completed` acredita respuesta, no edición verificada. Para edit, releer archivos o revisar pruebas en el equipo donde se ejecutó. No cerrar sesiones activas para entregar una consulta.
