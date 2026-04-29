#!/bin/bash
set -euo pipefail

DOMAIN="workcafe.tmpx.space"
WDIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$WDIR/frontend/dist"

if [ ! -d "$DIST" ]; then
    echo "ERROR: $DIST not found. Run: just build-public"
    exit 1
fi

echo "Setting up Apache2 VirtualHost for $DOMAIN"
echo "Static root: $DIST"

cat <<EOF | sudo tee /etc/apache2/sites-available/$DOMAIN.conf
<VirtualHost *:80>
    ServerName $DOMAIN

    # Block admin endpoints before proxy rules
    <Location "/api/services/">
        Require all denied
    </Location>
    <LocationMatch "^/api/[^/]+/log(\$|\\?)">
        Require all denied
    </LocationMatch>
    <Location "/api/gscraper/">
        Require all denied
    </Location>

    # Proxy API (read-only endpoints reach here)
    ProxyPreserveHost On
    ProxyPass /api/ http://127.0.0.1:13854/api/
    ProxyPassReverse /api/ http://127.0.0.1:13854/api/

    # Proxy images from Go API (Go serves them from data/seoul/)
    ProxyPass /images/ http://127.0.0.1:13854/images/
    ProxyPassReverse /images/ http://127.0.0.1:13854/images/
    <LocationMatch "^/images/">
        Header set Cache-Control "public, max-age=86400"
    </LocationMatch>

    # Serve static SPA
    DocumentRoot $DIST
    <Directory "$DIST">
        Options -Indexes
        AllowOverride None
        Require all granted
        # SPA fallback: unknown paths → index.html
        FallbackResource /index.html
    </Directory>

    # Immutable cache for hashed assets
    <LocationMatch "^/assets/">
        Header set Cache-Control "public, max-age=31536000, immutable"
    </LocationMatch>

    ErrorLog \${APACHE_LOG_DIR}/workcafe-error.log
    CustomLog \${APACHE_LOG_DIR}/workcafe-access.log combined
</VirtualHost>
EOF

# Enable required modules (likely already enabled)
sudo a2enmod proxy proxy_http headers rewrite 2>/dev/null || true

# Enable the site
sudo a2ensite $DOMAIN.conf

# Test and reload
echo "Testing Apache config..."
sudo apache2ctl configtest

echo "Reloading Apache..."
sudo systemctl reload apache2

echo ""
echo "Done. Apache now serves $DOMAIN on port 80."
echo ""
echo "Next: update tmpx nginx to forward port 80 instead of 5550."
echo "  On tmpx, change: proxy_pass http://100.111.210.47:5550"
echo "              to:  proxy_pass http://100.111.210.47:80"
