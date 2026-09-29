FROM python:3.12-slim

WORKDIR /app

# Logs immédiats (visibles en direct dans les logs Cloudflare)
ENV PYTHONUNBUFFERED=1

# Dependances Python
# Le certificat "proxy_ca" est facultatif : il ne sert qu'aux builds lancés
# derrière un proxy TLS (sessions Claude Code). Absent partout ailleurs.
COPY requirements.txt .
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -s /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir -r requirements.txt

# Code applicatif (voir .dockerignore)
COPY . .

# Port
EXPOSE 5000

# Demarrage
CMD ["python", "main.py"]
