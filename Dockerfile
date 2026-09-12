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

ENV HOME=/home/appuser
ENV HF_HOME=/home/appuser/.cache/huggingface
ENV PYTHONPATH=/app
# Build the Chroma knowledge base into the Docker image.
# These are build-time placeholders only; Render supplies the real
# runtime environment variables when the services start.
RUN mkdir -p /app/data/knowledge \
    /home/appuser/.cache/huggingface \
    /home/appuser/.cache/chroma \
    && DATABASE_URL="postgresql+asyncpg://build:build@localhost:5432/build" \
       WHATSAPP_TOKEN="build-placeholder" \
       WHATSAPP_PHONE_NUMBER_ID="build-placeholder" \
       WHATSAPP_VERIFY_TOKEN="build-placeholder" \
       WHATSAPP_APP_SECRET="build-placeholder" \
       GEMINI_API_KEY="build-placeholder" \
       RAZORPAY_KEY_ID="build-placeholder" \
       RAZORPAY_KEY_SECRET="build-placeholder" \
       RAZORPAY_WEBHOOK_SECRET="build-placeholder" \
       python scripts/build_knowledge_base.py --replace \
    && chown -R appuser:appuser /app /home/appuser

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]