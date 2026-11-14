# THE JOKER

- THE JOKER es un destino opcional y experimental: un agente OpenCode con un modelo Qwen propio en una GPU remota, operado por el propietario. Coordina la alcanza mediante su cliente local (`joker.cli` en `config.json`; sin valor útil por omisión: debe configurarse), que guarda la autenticación: Coordina no lee credenciales.
- Si el cliente no existe o no responde, la búsqueda devuelve `discovery_unavailable` y no abre otra sesión.
- La ruta nueva crea una conversación en THE JOKER. Cada encargo admite un turno (`rounds=1`).
- THE JOKER no está siempre encendido. Antes de entregar, Coordina revisa la GPU principal de QWEN con `qwenctl status`. Encenderla requiere que el propietario lo haya autorizado (configuración `joker`):
  - **Encendida, lista y con al menos 10 minutos de plazo:** la usa tal cual, sin renovar plazos de nadie.
  - **Encendida con menos de 10 minutos de plazo:** es una sesión ajena o por cerrarse; espera a que termine y no la renueva.
  - **Apagada:** la enciende por 60 minutos (`qwenctl start --node principal --minutes 60 --background`) y espera a que el servicio esté listo. Durante el arranque el encargo aparece como `starting_gpu`.
  - **Al terminar:** la respuesta se entrega de inmediato. Si fue Coordina quien encendió la GPU, abre un **período de gracia** (`joker.gracia_min`, 10 minutos por omisión) para que otro encargo la aproveche. Si llega uno, o hay encargos pendientes, la responsabilidad de apagarla pasa a ese encargo. Vencida la gracia sin encargos nuevos, la apaga si THE JOKER no tiene otros trabajos y verifica que quede EXITED, también si el encargo falló. El resultado queda en el recibo (`gpu`, `gpu_cierre`).
- **Modo lote:** para varios encargos a THE JOKER usa `coordinate_batch` (CLI: `coordina.py lote joker --workspace W --tarea "…" --tarea "…"`), con hasta 20 encargos. Van en orden a la misma conversación, la elegida o una nueva que abre el primero. La GPU se enciende como máximo una vez y se apaga, con gracia, después del último. Usa `job_wait` con cada id devuelto. Es la forma más económica de usar THE JOKER.
- La GPU tiene coste de ejecución; consultar la tarifa y los límites vigentes, sin asumir un precio fijo. Encarga trabajos acotados y agrúpalos en una misma sesión. Para lo que no exige el modelo propio, usa Claude o Codex.
- Las respuestas proceden de Qwen (`model_provider` en la transcripción). Trátalas como opinión o trabajo de un modelo local; no convertirlas por sí solas en datos de entrenamiento o evaluación sin la decisión correspondiente al proyecto.
