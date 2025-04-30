# 1. Imagem Base
FROM python:3.11-slim

# 2. Variáveis de Ambiente
ENV PYTHONUNBUFFERED=1 \
    # Porta DENTRO do container (pode ser sobrescrita pelo docker-compose)
    PORT=5000 \
    # Define o diretório do DB DENTRO do container (onde o volume será montado)
    DB_DIR=/app/db_data

# 3. Diretório de Trabalho
WORKDIR /app

# 4. Copiar e Instalar Dependências (Cache Otimizado)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 5. Copiar Código da Aplicação
COPY script.py .

# 6. Criar diretório para o volume do banco de dados DENTRO da imagem
RUN mkdir -p ${DB_DIR}

# 7. Expor a Porta do Container
EXPOSE ${PORT}

# 8. Comando de Execução
# Executa o script Python, que por sua vez chama waitress.serve()
CMD ["python", "./script.py"]