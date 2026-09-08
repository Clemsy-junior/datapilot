# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — build the static bundle with Node.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /build

# Manifest first, so `npm ci` is cached until dependencies actually change.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Empty base URL: in this deployment the API is same-origin, behind nginx.
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE

RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — serve the bundle with nginx. Node never reaches production.
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

# busybox wget is already in the image; no extra package for the healthcheck.
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
