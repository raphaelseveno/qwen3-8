FROM python:3.12-slim

WORKDIR /app
COPY gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY gateway /app/gateway
COPY skypilot /app/skypilot

EXPOSE 8787
CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8787"]
