FROM python:3.13-slim

# WeasyPrint (boletines en PDF) necesita Pango; se incluye una fuente para el PDF.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-inter \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Debe correr con DEBUG=0: así se genera el manifiesto de archivos estáticos que usa producción.
RUN DEBUG=0 SECRET_KEY=solo-para-construir python manage.py collectstatic --noinput

# Con CARGAR_DEMO=1 se crea el colegio de ejemplo en el primer arranque (si ya existe, no hace nada).
CMD ["sh", "-c", "python manage.py migrate --noinput && if [ \"$CARGAR_DEMO\" = \"1\" ]; then python manage.py datos_demo --actualizar || true; fi && gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 120"]
