# FURSAH backend image. Build from fursah-starter/:
#   docker build -t fursah-api .
#   docker run -p 8000:8000 --env-file backend/.env fursah-api
#
# The layout mirrors the repo (backend/ next to data/) because config.py
# finds the project root three folders above itself and reads data/ from there.
FROM python:3.12-slim

WORKDIR /srv/fursah/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY data /srv/fursah/data

# Render sets PORT; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
