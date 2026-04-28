#!/bin/bash

DOMAIN="workcafe.c.tmpx.space"
WDIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$WDIR/frontend/dist"

echo "Setting up Nginx static hosting for $DOMAIN..."
echo "Static root: $DIST"

cat <<EOF | sudo tee /etc/nginx/sites-available/$DOMAIN
server {
    listen 80;
    server_name $DOMAIN;

    # Block admin/internal endpoints from public
    location /api/services/ {
        return 403;
    }
    location ~ ^/api/[^/]+/log {
        return 403;
    }
    location /api/gscraper/ {
        return 403;
    }

    # API proxy (read-only endpoints only reach here)
    location /api/ {
        proxy_pass http://localhost:13854;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        add_header Cache-Control "no-cache";
    }

    # Images served by Go API (heavy files, long cache)
    location /images/ {
        proxy_pass http://localhost:13854;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        add_header Cache-Control "public, max-age=86400";
    }

    # Static SPA
    root $DIST;
    index index.html;
    location / {
        try_files \$uri \$uri/ /index.html;
        add_header Cache-Control "no-cache";
    }

    # Immutable hashed assets
    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/

echo "Testing Nginx configuration..."
sudo nginx -t

if [ $? -eq 0 ]; then
    sudo systemctl reload nginx
    echo "Done! $DOMAIN now serves static dist + proxies /api/ to :13854."
    echo "Run 'just stop frontend' — nginx serves the frontend now."
else
    echo "Nginx config failed. Check errors above."
    exit 1
fi
