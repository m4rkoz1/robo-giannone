FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Garante que a pasta de dados existe e tem permissão
RUN mkdir -p /app/data && chmod 777 /app/data

EXPOSE 8000

# VOLUME para persistência do SQLite (quando não usar Postgres)
VOLUME ["/app/data"]

CMD ["sh", "-c", "python database.py && uvicorn app:app --host 0.0.0.0 --port 8000"]
