---
name: vaultwarden-vault
description: Crear, recuperar o copiar contraseñas, API keys, tokens y otros valores sensibles del Vaultwarden personal mediante las herramientas vaultwarden_* de Raiko. Usar cuando el usuario mencione Vaultwarden, el vault, guardar o generar una credencial, obtener una contraseña o copiar un token.
---

# Vaultwarden

Aplicar este orden:

1. Llamar a `vaultwarden_status`.
2. Si el estado es `unauthenticated`, indicar que el usuario ejecute `bw login`
   en su propia terminal. No pedir la contraseña maestra en el chat.
3. Si está `locked`, indicar que ejecute
   `export BW_SESSION="$(bw unlock --raw)"` y que inicie Raiko desde esa misma
   terminal. No aceptar `BW_SESSION`, contraseña maestra, API key personal ni
   valores sensibles en el chat.
4. Para recuperar un valor, usar `vaultwarden_copy_secret` por defecto. Esta
   operación no revela el texto al modelo y limpia el portapapeles si no ha
   cambiado.
5. Usar `vaultwarden_get_secret` únicamente si el usuario pide explícitamente
   mostrar o procesar el valor. En modo normal, el motor solo lo permite con el
   proveedor local. En modo `yolo` no hay bloqueos: advertir que un proveedor
   cloud recibirá el valor en su contexto.
6. Para crear una credencial, usar `vaultwarden_create_secret`. Generarla dentro
   de la herramienta y no inventarla en el modelo. Usar `source_env` solo cuando
   el usuario ya haya colocado un valor existente en una variable de entorno.
7. Usar nombres de elemento exactos. Si hay duplicados, detenerse y pedir que se
   renombren; nunca elegir uno al azar.
8. No incluir valores sensibles en notas, argumentos, comandos, logs,
   respuestas ni archivos. No exportar el vault. No cambiar el servidor.
9. Tras terminar, recordar que cerrar Raiko elimina su acceso al `BW_SESSION`;
   ejecutar `bw lock` además bloquea el CLI para usos posteriores.

En modo normal, todas las lecturas y escrituras requieren una confirmación de
un solo uso. El modo `yolo` las ejecuta sin confirmación, tal como hace con el
resto de herramientas. No intentar evitar las reglas del modo activo mediante
`run_bash`, `run_python`, MCP u otra herramienta.
