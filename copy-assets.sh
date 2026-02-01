#!/bin/sh
# Copy built assets from image to shared volume
echo "Copying assets to shared volume..."
cp -rf /home/frappe/frappe-bench/apps/frappe/frappe/public/* /home/frappe/frappe-bench/sites/assets/frappe/ 2>/dev/null || true
cp -rf /home/frappe/frappe-bench/apps/nasiya365/nasiya365/public/* /home/frappe/frappe-bench/sites/assets/nasiya365/ 2>/dev/null || true
echo "Assets copied successfully!"
