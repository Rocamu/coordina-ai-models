# Otros adaptadores locales

Para Grok en este Mac y en un segundo equipo, lee [Grok y selección de equipo](grok.md). Las capacidades siguientes proceden de lectura del runtime, no de nuevas pruebas operativas.

- Muse (`muse`, alias `spark` o `meta-api`): API independiente, route=new (auto se normaliza), solo consult. No lee archivos por esta interfaz: incluir contexto en task. Su ejecutable/configuración impone credencial y presupuesto; sin comprobarlos no afirmar gasto permitido. No se validó funcionamiento en esta revisión.
- Meta AI (`meta-ai`, aliases meta/metaai): aunque backend reconoce el nombre, coordinate lo rechaza (destino deshabilitado). Se mantiene solo en el foro mediante meta-ai-canal-manual. No eludir el rechazo usando start_job directamente ni asignarle tareas pesadas.

No mezclar estos destinos con peers homónimos: un buzón no acredita entrega. Consultar la matriz y permisos reales antes de ejecutar.
