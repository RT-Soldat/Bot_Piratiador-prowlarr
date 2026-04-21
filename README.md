# Bot de Discord para Prowlarr

Bot ligero en Python 3.12 que expone los comandos `/buscar` y `/piratear` en Discord, consulta una instancia existente de Prowlarr y entrega magnet links o archivos `.torrent` como fallback. Está pensado para correr en Docker dentro de la misma red que el contenedor `prowlarr`, y sincroniza los slash commands tanto globalmente como por servidor para que aparezcan más rápido.

## Requisitos previos

- Docker y Docker Compose v2 instalados.
- Una instancia funcional de Prowlarr con indexers configurados.
- Un bot creado en Discord Developer Portal.

## Setup

1. Crea la aplicación y el bot en https://discord.com/developers/applications.
2. En el bot, habilita `Message Content Intent` si quieres que el bot también acepte mensajes de texto como `/buscar ubuntu` o `/piratear s04e01` además del slash command normal. Los slash commands por sí solos no dependen de este intent.
3. Invita el bot al servidor con scope `bot applications.commands` y permisos `Send Messages`, `Embed Links`, `Attach Files` y `Use Slash Commands`.
4. Copia `.env.example` a `.env` y completa las variables obligatorias.
5. Levanta el servicio:

```bash
docker compose up -d --build
```

6. Revisa los logs:

```bash
docker compose logs -f
```

## Variables de entorno

| Variable | Obligatoria | Descripcion |
| --- | --- | --- |
| `DISCORD_TOKEN` | Si | Token del bot de Discord |
| `ALLOWED_CHANNEL_ID` | Si | ID del canal donde se permiten `/buscar` y `/piratear` |
| `PROWLARR_URL` | Si | URL base de Prowlarr, por ejemplo `http://prowlarr:9696` |
| `PROWLARR_API_KEY` | Si | API key copiada desde Prowlarr |
| `PROWLARR_TIMEOUT` | No | Timeout de consultas a Prowlarr en segundos, por defecto `90` |
| `ATTACH_TORRENT_FILE` | No | Si vale `true`, intenta adjuntar también el archivo `.torrent` junto al magnet cuando esté disponible |
| `LOG_LEVEL` | No | Nivel de log, por defecto `INFO` |

## Comandos disponibles

- `/buscar <texto>`
- `/piratear <texto>`

También puedes escribir mensajes de texto con el mismo formato, por ejemplo `/buscar ubuntu 24.04`, si `Message Content Intent` está habilitado en Discord Developer Portal.

## Entrega de resultados

Por defecto, al seleccionar un resultado el bot entrega el magnet.

- Si el resultado trae `magnetUrl`, el bot lo usa.
- Si no hay `magnetUrl` pero sí `infoHash`, el bot construye un magnet compacto con trackers públicos.
- Si el `downloadUrl` de Prowlarr redirige a `magnet:`, el bot aprovecha ese magnet en vez de marcar error.
- El mensaje intenta incluir un botón `Abrir magnet` además del magnet en texto plano.

Si en el `.env` configuras:

```env
ATTACH_TORRENT_FILE=true
```

el bot intentará adjuntar también el archivo `.torrent` en el mismo mensaje cuando Prowlarr pueda descargarlo. Si el tracker solo redirige a un magnet y no entrega `.torrent`, el bot enviará únicamente el magnet.

## Troubleshooting

### El slash command no aparece

- Espera unos segundos tras iniciar el bot.
- Confirma que el bot tenga el scope `applications.commands`.
- Revisa `docker compose logs -f` para verificar que el `tree.sync()` se haya ejecutado sin errores.

### Falla la conexion a Prowlarr

- Verifica que `PROWLARR_URL` sea `http://prowlarr:9696` si ambos contenedores comparten la red `jellyfinarr-stack_default`.
- Comprueba que `PROWLARR_API_KEY` sea valida.
- Si Prowlarr tarda demasiado en responder, aumenta `PROWLARR_TIMEOUT` en el `.env`, por ejemplo a `120`.
- Si quieres recibir también el archivo `.torrent`, activa `ATTACH_TORRENT_FILE=true` en el `.env`.
- Asegurate de que la red externa exista en el host y de que el contenedor `prowlarr` este conectado a ella.

### El bot no puede enviar archivos

- Revisa que el bot tenga permisos `Attach Files` en el canal configurado.

### El comando responde fuera del canal esperado

- Confirma que `ALLOWED_CHANNEL_ID` sea el ID correcto del canal y que no tenga espacios extras en el `.env`.
