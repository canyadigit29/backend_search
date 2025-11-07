FROM python:3.11-slim

WORKDIR /app

 # System libs first, then install uv and sync deps from pyproject/lock.
COPY pyproject.toml .
RUN apt-get update && apt-get install -y \
        gawk \
        poppler-utils \
        tesseract-ocr \
        ghostscript \
        qpdf \
        pngquant \
    && apt-get install -y curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && /root/.local/bin/uv lock \
    && /root/.local/bin/uv sync --frozen --no-dev \
    && ln -s /root/.local/bin/uvicorn /usr/local/bin/uvicorn || true

 # Copy the rest of the project (after deps to leverage Docker layer caching)
COPY . .

# Convert line endings to Unix format to prevent script errors
RUN sed -i 's/\r$//' start.sh

# Make start script executable
RUN chmod +x start.sh

# Expose port for local use (optional)
ENV PORT=8000

# Run start script
CMD ["./start.sh"]
