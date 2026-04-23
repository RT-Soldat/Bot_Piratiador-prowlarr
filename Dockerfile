FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY discord_prowlarr_bot ./discord_prowlarr_bot

RUN useradd --create-home --shell /bin/bash botuser \
    && mkdir -p /app/data/registry \
    && chown -R botuser:botuser /app
USER botuser

CMD ["python", "-u", "-m", "discord_prowlarr_bot"]
