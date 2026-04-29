#!/bin/bash
set -euo pipefail

# Run this ON tmpx to update nginx: remove auth_basic, point to port 80

CONF=/etc/nginx/sites-available/workcafe.tmpx.space.conf

sudo tee "$CONF" > /dev/null << 'EOF'
server {
    server_name workcafe.tmpx.space;

    location / {
        proxy_pass http://100.111.210.47:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/workcafe.tmpx.space/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/workcafe.tmpx.space/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}
server {
    if ($host = workcafe.tmpx.space) { return 301 https://$host$request_uri; }
    listen 80;
    server_name workcafe.tmpx.space;
    return 404;
}
EOF

echo "Testing nginx config..."
sudo nginx -t

echo "Reloading nginx..."
sudo systemctl reload nginx

echo "Done. workcafe.tmpx.space now proxies to 100.111.210.47:80 (no auth)."
