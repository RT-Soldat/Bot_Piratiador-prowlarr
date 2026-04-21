# Bot de Discord para Prowlarr

Bot ligero en Python 3.12 que expone los comandos `/buscar` y `/piratear` en Discord, consulta una instancia existente de Prowlarr y entrega resultados mediante links HTTP clickeables, redirecciones a magnet y archivos `.torrent`. Está pensado para correr en Docker dentro de la misma red que el contenedor `prowlarr`, y sincroniza los slash commands tanto globalmente como por servidor para que aparezcan más rápido.

## Requisitos previos

- Docker y Docker Compose v2 instalados.
- Una instancia funcional de Prowlarr con indexers configurados.
- Un bot creado en Discord Developer Portal.

## Setup

1. Crea la aplicación y el bot en https://discord.com/developers/applications.
2. En el bot, habilita `Message Content Intent` si quieres que el bot también acepte mensajes de texto como `/buscar ubuntu` o `/piratear s04e01` además del slash command normal. Los slash commands por sí solos no dependen de este intent.
3. Invita el bot al servidor con scope `bot applications.commands` y permisos `Send Messages`, `Embed Links`, `Attach Files` y `Use Slash Commands`.
4. Copia `.env.example` a `.env` y completa las variables obligatorias. Si quieres botones clickeables en Discord, configura también `BOT_PUBLIC_BASE_URL` con una URL alcanzable desde afuera.
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
| `BOT_HTTP_LISTEN_HOST` | No | Host donde escucha el servidor HTTP interno del bot, por defecto `0.0.0.0` |
| `BOT_HTTP_LISTEN_PORT` | No | Puerto HTTP para links clickeables y healthcheck, por defecto `9987` |
| `BOT_PUBLIC_BASE_URL` | No | URL pública base usada para construir botones `http(s)://` en Discord |
| `TORRENT_FETCH_TIMEOUT` | No | Tiempo máximo en segundos para resolver metadata vía DHT, por defecto `45` |
| `LIBTORRENT_LISTEN_PORT` | No | Puerto que usa libtorrent para DHT, por defecto `6881` |
| `LOG_LEVEL` | No | Nivel de log, por defecto `INFO` |

## Comandos disponibles

- `/buscar <texto>`
- `/piratear <texto>`

También puedes escribir mensajes de texto con el mismo formato, por ejemplo `/buscar ubuntu 24.04`, si `Message Content Intent` está habilitado en Discord Developer Portal.

## Entrega de resultados

Al seleccionar un resultado, el bot intenta resolver la mejor salida disponible:

- Si Prowlarr devuelve un `.torrent`, lo usa directamente.
- Si el indexer solo ofrece magnet, el bot intenta obtener metadata vía DHT usando `libtorrent`.
- Si `BOT_PUBLIC_BASE_URL` está configurado, publica botones HTTP clickeables:
  - `Descargar .torrent` apunta a `/t/<id>`
  - `Abrir magnet` apunta a `/m/<id>` y redirige a `magnet:?`
- Si no hay URL pública, el bot cae a adjuntos o al magnet en texto plano.

Si además configuras:

```env
ATTACH_TORRENT_FILE=true
```

el bot adjuntará también el archivo `.torrent` cuando lo tenga disponible, incluso si tuvo que generarlo localmente desde el magnet.

El bot expone además:

```bash
curl http://localhost:9987/health
```

para verificar que el servidor HTTP embebido está arriba.

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

### Los botones no funcionan

- Confirma que `BOT_PUBLIC_BASE_URL` apunte al host correcto y no termine con `/`.
- Verifica que el puerto `9987` esté publicado en Docker y accesible desde afuera.
- Si el `.torrent` tarda mucho en generarse para magnets viejos o con poco swarm, prueba subiendo `TORRENT_FETCH_TIMEOUT`.
- Comprueba el endpoint `http://<tu-host>:9987/health`.

### El comando responde fuera del canal esperado

- Confirma que `ALLOWED_CHANNEL_ID` sea el ID correcto del canal y que no tenga espacios extras en el `.env`.
