FROM python:3.12-slim

# Dependências de sistema do WeasyPrint (geração de PDF) e `tzdata`, sem o qual
# o TZ do compose é ignorado em silêncio e a auditoria aparece 3h adiantada (o
# banco grava em UTC; a tela converte para o fuso do servidor).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
        libffi-dev libcairo2 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# O desenvolvimento é em Windows: normaliza CRLF, senão o shell do container
# falha com "\r: not found" na primeira linha do script.
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Diretório do banco SQLite (o compose monta um volume aqui).
RUN mkdir -p /dados

EXPOSE 8000
CMD ["/app/entrypoint.sh"]
