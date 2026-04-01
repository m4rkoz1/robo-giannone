# Usa uma imagem oficial do Python, bem leve (alpine ou slim)
FROM python:3.12-slim

# Define a pasta de trabalho dentro do container
WORKDIR /app

# Copia os arquivos de configuração de dependências e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o projeto para a pasta /app
COPY . .

# Expõe a porta que o FastApi vai rodar
EXPOSE 8000

# Executa as migrações/criação do banco E liga o servidor
CMD ["sh", "-c", "python database.py && uvicorn app:app --host 0.0.0.0 --port 8000"]
