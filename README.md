# Bot de Discord para Prowlarr

Bot ligero en Python 3.12 que expone el comando `/buscar` en Discord, consulta una instancia existente de Prowlarr y entrega magnet links o archivos `.torrent` como fallback. Está pensado para correr en Docker dentro de la misma red que el contenedor `prowlarr`.

## Requisitos previos

- Docker y Docker Compose v2 instalados.
- Una instancia funcional de Prowlarr con indexers configurados.
- Un bot creado en Discord Developer Portal.

## Setup

1. Crea la aplicación y el bot en https://discord.com/developers/applications.
2. En el bot, habilita `Message Content Intent` si quieres dejar preparado el bot para futuras extensiones. Para `/buscar` no es estrictamente necesario.
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
| `ALLOWED_CHANNEL_ID` | Si | ID del canal donde se permite `/buscar` |
| `PROWLARR_URL` | Si | URL base de Prowlarr, por ejemplo `http://prowlarr:9696` |
| `PROWLARR_API_KEY` | Si | API key copiada desde Prowlarr |
| `LOG_LEVEL` | No | Nivel de log, por defecto `INFO` |

## Comando disponible

- `/buscar <texto>`

## Troubleshooting

### El slash command no aparece

- Espera unos segundos tras iniciar el bot.
- Confirma que el bot tenga el scope `applications.commands`.
- Revisa `docker compose logs -f` para verificar que el `tree.sync()` se haya ejecutado sin errores.

### Falla la conexion a Prowlarr

- Verifica que `PROWLARR_URL` sea `http://prowlarr:9696` si ambos contenedores comparten la red `jellyfinarr-stack_default`.
- Comprueba que `PROWLARR_API_KEY` sea valida.
- Asegurate de que la red externa exista en el host y de que el contenedor `prowlarr` este conectado a ella.

### El bot no puede enviar archivos

- Revisa que el bot tenga permisos `Attach Files` en el canal configurado.

### El comando responde fuera del canal esperado

- Confirma que `ALLOWED_CHANNEL_ID` sea el ID correcto del canal y que no tenga espacios extras en el `.env`.
