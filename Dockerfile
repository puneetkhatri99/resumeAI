# One image for Render: builds the React frontend, then runs FastAPI, which serves both the
# built frontend (/) and the API (/api) from one origin.

# Stage 1: build the frontend (tsc + vite build -> /frontend/dist).
FROM node:24-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: the backend, with headless Chromium for the branded PDF.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./
# --with-deps installs the system libraries Chromium needs (libnss3 and others).
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps --only-shell chromium
COPY backend/ ./
COPY --from=frontend /frontend/dist ./static

# Render sets PORT. Migrations run on every start; when the database is at head it's a no-op.
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
