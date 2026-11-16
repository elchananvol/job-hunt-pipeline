# Single-container image: FastAPI backend serves the built React UI.
# Stage 1 builds ui/dist with Node; stage 2 runs Python + serves everything.
FROM node:20-alpine AS ui-build
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

# git is needed only to pip-install python-jobspy from GitHub.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY examples/ ./examples/
COPY scripts/init_personal_files.py ./scripts/init_personal_files.py
COPY --from=ui-build /ui/dist ./ui/dist

# db/ + personal_files/ are mounted as volumes at runtime (see docker-compose.yml)
# so SQLite data, CV outputs and lesson files survive rebuilds.
RUN mkdir -p db personal_files/cv_generated

EXPOSE 8000
CMD ["sh", "-c", "python scripts/init_personal_files.py && exec python -m uvicorn server.server:app --host 0.0.0.0 --port 8000"]
