#!/bin/bash
# Post-deployment setup script for Nasiya365
# Run this after all services are up

SITE_NAME="${1:-nasiya365.local}"
ADMIN_PASSWORD="${2:-admin}"
DB_ROOT_PASSWORD="${3:-admin}"

echo "🚀 Setting up Nasiya365..."

# Wait for MariaDB to be ready
echo "⏳ Waiting for MariaDB..."
sleep 10

# Create site
echo "📦 Creating site: $SITE_NAME"
bench new-site "$SITE_NAME" \
    --db-root-password "$DB_ROOT_PASSWORD" \
    --admin-password "$ADMIN_PASSWORD" \
    --no-mariadb-socket

# Install app
echo "📥 Installing nasiya365 app..."
bench --site "$SITE_NAME" install-app nasiya365

# Run migrations
echo "🔄 Running migrations..."
bench --site "$SITE_NAME" migrate

# Clear cache
echo "🧹 Clearing cache..."
bench --site "$SITE_NAME" clear-cache

# Set as default site
echo "⭐ Setting as default site..."
bench use "$SITE_NAME"

echo "✅ Setup complete!"
echo ""
echo "🌐 Access your site at: https://$SITE_NAME"
echo "👤 Login: Administrator"
echo "🔑 Password: $ADMIN_PASSWORD"
