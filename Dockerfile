# A container that runs the proxy with no Core and no credentials baked in: set them at runtime from
# the dashboard at /__proxy/. Guard a public instance with SFP_CONTROL_TOKEN.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PORT=8080
EXPOSE 8080

# Run the already-installed entrypoint directly - no sync, no network, fast start.
# --host 0.0.0.0 so the platform can reach it; the port comes from $PORT.
CMD [".venv/bin/signaling-fault-proxy", "--host", "0.0.0.0"]
