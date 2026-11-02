FROM node:24-slim AS agent-browser

# El paquete de npm trae el binario nativo de cada plataforma. Se copia solo el
# de esta arquitectura: agent-browser no necesita Node para correr.
RUN npm install -g agent-browser@0.27.0 \
    && arch="$(dpkg --print-architecture | sed 's/amd64/x64/')" \
    && cp "$(npm root -g)/agent-browser/bin/agent-browser-linux-${arch}" /agent-browser

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

# `agent-browser install` baja Chrome for Testing, que no tiene build para Linux
# ARM. El Chromium de Debian funciona en x64 y en ARM.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY --from=agent-browser /agent-browser /usr/local/bin/agent-browser

# Chromium no puede usar su sandbox dentro del contenedor sin permisos extra, y
# el /dev/shm de Docker (64 MB) le queda chico.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
    AGENT_BROWSER_ARGS=--no-sandbox,--disable-dev-shm-usage

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown app:app /app/data

USER app

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uv", "run", "--no-sync", "uvicorn", "financial_agent.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000", "--no-access-log"]
