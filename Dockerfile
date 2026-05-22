FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Playwright system dependencies for Chromium (~400MB — unavoidable)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY rentbuster/ ./rentbuster/
COPY profiles/ ./profiles/
COPY schema.sql ./

RUN uv sync --frozen --no-dev

# Install Playwright Chromium browser
RUN /app/.venv/bin/playwright install chromium

RUN useradd --create-home --uid 1000 rentbuster \
    && chown -R rentbuster:rentbuster /app
USER rentbuster

HEALTHCHECK --interval=5m --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import rentbuster; print(rentbuster.__version__)" || exit 1

ENTRYPOINT ["python", "-m", "rentbuster"]
CMD ["run"]
