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

# Copy cached assets to sites directory (from Dockerfile build)
if [ -d "/home/frappe/assets_cache" ] && [ ! -d "sites/assets/nasiya365" ]; then
    echo "Copying cached assets..."
    cp -r /home/frappe/assets_cache/* sites/assets/ 2>/dev/null || true
fi

# Check if site already exists
if [ -f "sites/${SITE_NAME}/site_config.json" ]; then
    echo "Site ${SITE_NAME} already exists. Running migrations..."
    bench --site ${SITE_NAME} migrate
    echo "Migrations complete."
else
    echo "Site ${SITE_NAME} does not exist. Creating..."
    
    # Wait for MariaDB to be ready
    echo "Waiting for database to be ready..."
    for i in {1..30}; do
        if mysqladmin ping -h ${DB_HOST} -u root -p${DB_ROOT_PASSWORD} --silent 2>/dev/null; then
            echo "Database is ready!"
            break
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
fi

echo "Init complete. Exiting."
