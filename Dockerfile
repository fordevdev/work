FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir markdown

WORKDIR /app
COPY index.html server.py ./
COPY notes/ ./notes/
COPY static/ ./static/
COPY concepts/ ./concepts/

EXPOSE 8080
CMD ["python3", "server.py"]
