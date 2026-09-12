FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser \
    && useradd -r -g appuser -m -d /home/appuser appuser

COPY requirements.txt .
RUN pip install --no-cache-dir \
    --default-timeout=300 \
    --retries=10 \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

COPY . .

# Create writable directories for the application and Hugging Face cache
RUN mkdir -p /app/data/knowledge \
    /home/appuser/.cache/huggingface \
    /home/appuser/.cache/chroma \
    && chown -R appuser:appuser /app /home/appuser

ENV HOME=/home/appuser
ENV HF_HOME=/home/appuser/.cache/huggingface

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]