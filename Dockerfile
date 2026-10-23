FROM python:3.13-slim

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    MB_CONFIG_DIR=/config \
    MB_MEDIA_ROOT=/media \
    MB_PORT=8866

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY app ./app

VOLUME ["/config", "/media"]
EXPOSE 8866

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8866/api/version',timeout=5)" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8866"]
