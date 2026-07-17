# === Stage 1: Build frontend (Node) ===
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci --omit=optional

COPY . .
RUN npm run build

# === Stage 2: Serve from Nginx ===
FROM nginx:1.27-alpine

# Remove default nginx config
RUN rm -rf /etc/nginx/conf.d/default.conf

# Custom nginx config — SPA support, gzip, security headers
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen       5173;
    server_name  _;
    root         /usr/share/nginx/html;
    index        index.html;

    # Security headers (matching backend security_headers.py)
    add_header X-Content-Type-Options  "nosniff" always;
    add_header X-Frame-Options         "DENY"    always;
    add_header Referrer-Policy         "strict-origin-when-cross-origin" always;

    # Gzip
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml image/svg+xml;

    # API proxy (pass /api/* to backend in production-like mode)
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
        proxy_buffering off;  # SSE streaming
    }

    # SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 5173
CMD ["nginx", "-g", "daemon off;"]
