FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends su-exec \
    && rm -rf /var/lib/apt/lists/*

COPY discord_prowlarr_bot ./discord_prowlarr_bot
COPY entrypoint.sh .

RUN useradd --create-home --shell /bin/bash botuser \
    && chown -R botuser:botuser /app \
    && chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "-u", "-m", "discord_prowlarr_bot"]
