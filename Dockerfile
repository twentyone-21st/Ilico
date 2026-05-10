FROM python:3.10-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código de la aplicación
COPY . .

# Railway y Cloud Run inyectan PORT automáticamente
ENV PORT=8080

# Arrancar con gunicorn (servidor de producción)
# --timeout 120: el modelo tarda ~30s en entrenar la primera vez
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 300 --graceful-timeout 30 app:app
