set -eu
cd "$(dirname "$0")"

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

if [ -e data ] && [ ! -w data ]; then
    echo "No se puede escribir en data/. Si viene de Docker: sudo chown -R $(id -un): data" >&2
    exit 1
fi

exec "$UV" run --frozen --no-dev --env-file .env \
    uvicorn financial_agent.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
