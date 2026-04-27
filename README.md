# Bot de Discord para Prowlarr

Bot en Python 3.12 que expone `/buscar`, `/piratear` y `/status` en Discord, consulta Prowlarr y entrega torrents como botón `Abrir magnet` y/o archivo `.torrent` adjunto. Corre en Docker en la misma red que Prowlarr.

## Requisitos previos

- Docker y Docker Compose v2 instalados.
- Una instancia funcional de Prowlarr con indexers configurados.
- Un bot creado en Discord Developer Portal.

## Setup

1. Crea la aplicación y el bot en https://discord.com/developers/applications.
2. En el bot, habilita `Message Content Intent` si quieres que también acepte mensajes de texto como `/buscar ubuntu` además del slash command. Los slash commands no dependen de este intent.
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

El directorio `./data/` se crea automáticamente al arrancar. Ahí se persiste el registry de links.

## Variables de entorno

### Obligatorias

| Variable | Descripción |
| --- | --- |
| `DISCORD_TOKEN` | Token del bot de Discord |
| `ALLOWED_CHANNEL_ID` | ID del canal donde se permiten los comandos |
| `PROWLARR_URL` | URL base de Prowlarr, por ejemplo `http://prowlarr:9696` |
| `PROWLARR_API_KEY` | API key copiada desde Prowlarr → Settings → General |

### Opcionales

| Variable | Default | Descripción |
| --- | --- | --- |
| `PROWLARR_TIMEOUT` | `90` | Timeout de consultas a Prowlarr en segundos. Subir a `180` si hay indexers lentos |
| `ATTACH_TORRENT_FILE` | `false` | Si vale `true`, adjunta el `.torrent` además del botón de magnet |
| `BOT_HTTP_LISTEN_HOST` | `0.0.0.0` | Host del servidor HTTP interno |
| `BOT_HTTP_LISTEN_PORT` | `9987` | Puerto del servidor HTTP interno |
| `BOT_PUBLIC_BASE_URL` | — | URL pública base para los botones `Abrir magnet`, ej. `http://errete.ddns.net:9987` |
| `TORRENT_FETCH_TIMEOUT` | `45` | Timeout en segundos para resolver metadata vía DHT |
| `LIBTORRENT_LISTEN_PORT` | `6881` | Puerto que usa libtorrent para DHT |
| `LOG_LEVEL` | `INFO` | Nivel de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `REGISTRY_TTL_SECONDS` | `604800` | Vida útil de los links `/m/` en segundos (default 7 días). Al vencer se borra el archivo y el mensaje de Discord |
| `REGISTRY_PURGE_INTERVAL_SECONDS` | `300` | Intervalo del purge periódico en segundos |
| `REGISTRY_DATA_DIR` | `/app/data/registry` | Directorio de persistencia del registry dentro del contenedor |
| `RATE_LIMIT_CALLS` | `5` | Máximo de búsquedas permitidas por usuario en la ventana |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Ventana del rate limit en segundos |
| `SEARCH_RESULT_LIMIT` | `10` | Resultados a mostrar por defecto; usa `0` para no limitar |
| `SUBTITLE_ENABLED` | `true` | Busca subtítulos automáticamente tras entregar un resultado |
| `OPENSUBTITLES_API_KEY` | — | API key de OpenSubtitles.com |
| `OPENSUBTITLES_USERNAME` | — | Usuario de OpenSubtitles.com |
| `OPENSUBTITLES_PASSWORD` | — | Contraseña de OpenSubtitles.com |
| `SUBTITLE_LANGUAGES` | `es` | Idiomas separados por coma; el primero se usa como idioma principal |
| `TRANSLATION_ENABLED` | `true` | Traduce desde inglés si no hay subtítulo en el idioma principal |
| `TRANSLATION_PROVIDER` | `google` | Proveedor de traducción: `google` o `deepl` |
| `DEEPL_API_KEY` | — | API key de DeepL si `TRANSLATION_PROVIDER=deepl` |
| `SUBTITLE_FETCH_TIMEOUT` | `30` | Timeout de búsqueda y descarga de subtítulos en segundos |

## Comandos disponibles

| Comando | Descripción |
| --- | --- |
| `/buscar <query>` | Busca torrents en Prowlarr |
| `/piratear <query>` | Alias de `/buscar` |
| `/status` | Muestra estado del bot: ping a Prowlarr, libtorrent, uptime y entradas activas |

También podés escribir `/buscar <query>` como mensaje de texto si `Message Content Intent` está habilitado. Los filtros opcionales solo están disponibles en slash commands, salvo `--avanzada`, que también funciona en texto.

### Filtros opcionales en `/buscar` y `/piratear`

| Parámetro | Tipo | Descripción |
| --- | --- | --- |
| `categoria` | Choice | `peliculas`, `series`, `musica`, `software`, `libros` |
| `min_seeders` | Entero | Oculta resultados con menos seeders que este valor |
| `año` | Entero | Filtra títulos que no contengan ese año |
| `privada` | Bool | `true` para que solo vos veas los resultados (ephemeral) |
| `avanzada` | Bool | `true` para traer todos los resultados en vez del límite default |

Los resultados se pueden re-ordenar con los botones **🌱 Seeders**, **📦 Tamaño** y **🗓️ Fecha**. Solo el autor de la búsqueda puede interactuar con la vista.

## Entrega de resultados

Al seleccionar un resultado, el bot intenta en orden:

1. Usar el `.torrent` directo si Prowlarr lo devuelve.
2. Generar el `.torrent` vía DHT (solo si `ATTACH_TORRENT_FILE=true`).
3. Publicar un botón `Abrir magnet` vía `/m/<id>` si `BOT_PUBLIC_BASE_URL` está configurado.

Los links `/m/<id>` se persisten en disco (`./data/registry/`) y viven `REGISTRY_TTL_SECONDS` (default 7 días). Al vencer, el bot borra el archivo del disco y elimina el mensaje de Discord que contenía el botón.

## Subtítulos

El bot puede buscar subtítulos automáticamente en OpenSubtitles.com después de entregar el magnet o `.torrent`. Requiere una cuenta gratuita y una API key creada en https://www.opensubtitles.com/es/consumers.

`SUBTITLE_ENABLED` viene activo por defecto, pero si falta `OPENSUBTITLES_API_KEY`, `OPENSUBTITLES_USERNAME` u `OPENSUBTITLES_PASSWORD`, el bot arranca igual, escribe un warning y desactiva solo esta función. El free tier suele alcanzar para uso personal, con un límite de 20 descargas por día.

`SUBTITLE_LANGUAGES=es` busca subtítulos en español. Podés usar varios idiomas separados por coma, por ejemplo `es,en`. Si no encuentra subtítulo en el primer idioma y `TRANSLATION_ENABLED=true`, intenta descargar uno en inglés y traducirlo al idioma principal. `TRANSLATION_PROVIDER=google` no requiere API key; para DeepL usa `TRANSLATION_PROVIDER=deepl` y completa `DEEPL_API_KEY`.

El servidor HTTP expone también un healthcheck:

```bash
curl http://127.0.0.1:19987/health
```

## Nginx

Si querés exponer el servidor HTTP públicamente detrás de Nginx:

- Publicá el contenedor solo en loopback: `127.0.0.1:19987:9987`
- Configurá `BOT_PUBLIC_BASE_URL=http://errete.ddns.net:9987`
- Hacé proxy con Nginx desde `:9987` hacia `http://127.0.0.1:19987`

## Troubleshooting

### El slash command no aparece

- Esperá unos segundos tras iniciar el bot.
- Confirmá que el bot tenga el scope `applications.commands`.
- Revisá los logs: `docker compose logs -f` y buscá la línea `Se sincronizaron N slash commands globales`.

### Prowlarr tarda demasiado / ReadTimeout

- Aumentá `PROWLARR_TIMEOUT` en el `.env`, por ejemplo a `180`.
- En Prowlarr revisá qué indexers tienen alta latencia o errores (Prowlarr → System → Tasks o Indexers).

### Falla la conexión a Prowlarr

- Verificá que `PROWLARR_URL` use el nombre del servicio Docker, ej. `http://prowlarr:9696`.
- Comprobá que `PROWLARR_API_KEY` sea válida.
- Asegurate de que la red externa `jellyfinarr-stack_default` exista y que el contenedor `prowlarr` esté conectado a ella.

### El bot no puede enviar archivos

- Revisá que el bot tenga el permiso `Attach Files` en el canal configurado.

### Los botones `Abrir magnet` no funcionan

- Confirmá que `BOT_PUBLIC_BASE_URL` no termine con `/` y apunte al host correcto.
- Verificá el backend con `curl http://127.0.0.1:19987/health`.
- Si el link venció (más de 7 días por default), el mensaje ya fue borrado automáticamente.

### El comando responde fuera del canal esperado

- Confirmá que `ALLOWED_CHANNEL_ID` sea el ID correcto del canal y que no tenga espacios en el `.env`.

### Error de permisos en `/app/data`

- El entrypoint del contenedor crea el directorio y le asigna permisos automáticamente. Si el error persiste, verificá que el volumen `./data` en el host sea accesible por Docker.
