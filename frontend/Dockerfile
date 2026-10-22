FROM golang:1.26.10-alpine@sha256:27f2d3b83cfc5803c8078f8e730af0f7c431bf067afaec8f60a36fb763187cba AS esbuild
RUN CGO_ENABLED=0 GOMAXPROCS=2 GOTOOLCHAIN=local go install -p=2 -trimpath github.com/evanw/esbuild/cmd/esbuild@v0.25.12

FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install --global --ignore-scripts npm@11.17.0 && npm ci --ignore-scripts
COPY --from=esbuild /go/bin/esbuild /usr/local/bin/esbuild
RUN node -e "require('node:fs').copyFileSync('/usr/local/bin/esbuild', require.resolve('@esbuild/linux-' + process.arch + '/bin/esbuild'))" \
    && /usr/local/bin/esbuild --version
ENV ESBUILD_BINARY_PATH=/usr/local/bin/esbuild
COPY . .
RUN npm run build

FROM nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
