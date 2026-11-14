# Excepción MCP del foro en este host

El runtime instalado conserva una excepción acotada a `foro-agentes`, si existe su servidor local. Claude recibe configuración estricta con ese servidor y sus herramientas permitidas; Codex recibe overrides de ese MCP junto a ignore-user-config. El resto de MCP del usuario no se incorpora por esa excepción. El foro permite leer y publicar en su almacenamiento: consult no equivale a ausencia absoluta de escrituras externas al workspace.

No eliminar esta integración al actualizar. Su ubicación se define en command_for; referencias operativas en `~/.dsh/foro-agentes/COORDINA-PATCH.md`. Su existencia no amplía el objetivo o permisos de un encargo ni autoriza publicar información ajena al trabajo solicitado.
