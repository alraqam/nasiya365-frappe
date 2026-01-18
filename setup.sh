#!/bin/bash
# Setup script for my.nasiya365.uz
# Run this in the backend container after deployment

SITE_NAME="my.nasiya365.uz"
ADMIN_PASSWORD="${1:-Nasiya365Admin}"
DB_ROOT_PASSWORD="${2:-your_mariadb_password}"

echo "🚀 Setting up Nasiya365 for $SITE_NAME..."

# Wait for services
sleep 10

# Create site
echo "📦 Creating site..."
bench new-site "$SITE_NAME" \
    --db-host mariadb \
    --db-root-password "$DB_ROOT_PASSWORD" \
    --admin-password "$ADMIN_PASSWORD" \
    --no-mariadb-socket

# Install app
echo "📥 Installing nasiya365..."
bench --site "$SITE_NAME" install-app nasiya365

# Migrate
echo "🔄 Running migrations..."
bench --site "$SITE_NAME" migrate

# Clear cache
echo "🧹 Clearing cache..."
bench --site "$SITE_NAME" clear-cache

# Set default
bench use "$SITE_NAME"

echo ""
echo "✅ Setup complete!"
echo "🌐 URL: https://my.nasiya365.uz"
echo "👤 Login: Administrator"
echo "🔑 Password: $ADMIN_PASSWORD"
