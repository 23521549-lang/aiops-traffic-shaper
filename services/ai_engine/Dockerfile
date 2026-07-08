FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends     curl     && rm -rf /var/lib/apt/lists/*

COPY services/ai_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN addgroup --system --gid 1000 aiops     && adduser --system --uid 1000 --gid 1000 --no-create-home aiops     && chown -R aiops:aiops /app

USER aiops

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "services.ai_engine.main:app",      "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
