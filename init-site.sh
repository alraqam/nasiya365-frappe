#!/bin/bash
# Nasiya365 Site Setup Script
# This script is run by the init container on first deployment
# It creates the Frappe site if it doesn't exist

set -e

SITE_NAME="${FRAPPE_SITE_NAME:-my.nasiya365.uz}"
DB_HOST="${DB_HOST:-mariadb}"
DB_ROOT_PASSWORD="${DB_ROOT_PASSWORD:-NasiyaSecure2026}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}"

echo "=== Nasiya365 Site Setup ==="
echo "Site: ${SITE_NAME}"
echo "DB Host: ${DB_HOST}"

cd /home/frappe/frappe-bench

# Config Redis
echo "Configuring Redis..."
bench set-config -g redis_cache "redis://${REDIS_CACHE:-redis-cache:6379}"
bench set-config -g redis_queue "redis://${REDIS_QUEUE:-redis-queue:6379}"
bench set-config -g redis_socketio "redis://${REDIS_QUEUE:-redis-queue:6379}"

# Copy cached assets to sites directory (from Dockerfile build)
if [ -d "/home/frappe/assets_cache" ] && [ ! -d "sites/assets/nasiya365" ]; then
    echo "Copying cached assets..."
    cp -r /home/frappe/assets_cache/* sites/assets/ 2>/dev/null || true
fi

# Check if site already exists
if [ -f "sites/${SITE_NAME}/site_config.json" ]; then
    echo "Site ${SITE_NAME} already exists."
    # Try to migrate. If it fails (e.g. DB user missing), we should probably recreate the site.
    if bench --site ${SITE_NAME} migrate; then
        echo "Migrations complete. Site is healthy."
        exit 0
    else
        echo "WARNING: Migration failed! This usually means the 'sites' volume has a config that doesn't match the database (e.g. DB was reset)."
        echo "Backing up broken site to sites/${SITE_NAME}_bak_$(date +%s) and forcing re-creation..."
        mv "sites/${SITE_NAME}" "sites/${SITE_NAME}_bak_$(date +%s)"
    fi
fi

# Determine if we should create the site (it might have been moved above, or didn't exist)
echo "Site ${SITE_NAME} does not exist (or was broken). Creating..."

# Wait for MariaDB to be ready
echo "Waiting for database to be ready..."
# Wait for MariaDB to be ready
echo "Waiting for database to be ready..."
for i in {1..30}; do
    # Try a real query to verify credentials strictly
    if mysql -h ${DB_HOST} -u root -p"${DB_ROOT_PASSWORD}" -e "SELECT 1" >/dev/null 2>&1; then
        echo "Database is ready and credentials are accepted!"
        break
    fi
    
    # Check if the failure is due to auth error
    if mysql -h ${DB_HOST} -u root -p"${DB_ROOT_PASSWORD}" -e "SELECT 1" 2>&1 | grep -q "Access denied"; then
        echo "CRITICAL ERROR: Access denied for MariaDB root user!"
        echo "The password configured in 'easypanel-template.json' (DB_ROOT_PASSWORD) does not match the actual database password."
        echo "ACTION REQUIRED: You must DELETE the 'mariadb' service volume in EasyPanel to reset the password."
        echo "Debug Info: DB_HOST=${DB_HOST}"
        exit 1
    fi
    echo "Waiting for database... (${i}/30)"
    sleep 2
done

# Create the site
echo "Creating new site..."
bench new-site ${SITE_NAME} \
    --db-host ${DB_HOST} \
    --db-root-password ${DB_ROOT_PASSWORD} \
    --admin-password ${ADMIN_PASSWORD} \
    --no-mariadb-socket

echo "Site created successfully!"

# Install nasiya365 app
echo "Installing nasiya365 app..."
bench --site ${SITE_NAME} install-app nasiya365

# Run migrations
echo "Running migrations..."
bench --site ${SITE_NAME} migrate

# Set as default site
bench use ${SITE_NAME}

echo "=== Setup Complete ==="
echo "You can now access the site at https://${SITE_NAME}"
echo "Login with:"
echo "  Username: Administrator"
echo "  Password: ${ADMIN_PASSWORD}"

echo "Init complete. Exiting."
