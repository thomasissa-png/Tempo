FROM python:3.12-slim

WORKDIR /app

# Dependances systeme
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Dependances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code applicatif
COPY . .

# Port
EXPOSE 5000

# Demarrage
CMD ["python", "main.py"]
