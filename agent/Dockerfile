FROM golang:1.26.10-alpine@sha256:27f2d3b83cfc5803c8078f8e730af0f7c431bf067afaec8f60a36fb763187cba AS esbuild
RUN CGO_ENABLED=0 GOMAXPROCS=2 GOTOOLCHAIN=local go install -p=2 -trimpath github.com/evanw/esbuild/cmd/esbuild@v0.25.12

FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY backend/requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir -r /opt/requirements.txt
WORKDIR /workspace/backend
USER 1000:1000

FROM node:22-trixie-slim@sha256:8eb619e402eaede1748a3c8173bffab391592812e85136452f781741c7242ddf AS frontend
WORKDIR /opt/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --global --ignore-scripts npm@11.17.0 \
    && npm ci --ignore-scripts --no-audit --no-fund \
    && rm -rf /usr/local/lib/node_modules /opt/yarn-* \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /usr/local/bin/yarn /usr/local/bin/yarnpkg
COPY --from=esbuild /go/bin/esbuild /usr/local/bin/esbuild
RUN node -e "require('node:fs').copyFileSync('/usr/local/bin/esbuild', require.resolve('@esbuild/linux-' + process.arch + '/bin/esbuild'))" \
    && /usr/local/bin/esbuild --version
ENV ESBUILD_BINARY_PATH=/usr/local/bin/esbuild
WORKDIR /workspace/frontend
USER 1000:1000
