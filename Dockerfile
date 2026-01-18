# Nasiya365 Production Dockerfile
FROM frappe/erpnext:v16

# Switch to root to copy and set permissions
USER root

# Copy custom app
COPY ./nasiya365 /home/frappe/frappe-bench/apps/nasiya365

# Set proper ownership
RUN chown -R frappe:frappe /home/frappe/frappe-bench/apps/nasiya365

# Switch back to frappe user
USER frappe

# Install custom app
RUN cd /home/frappe/frappe-bench && \
    ./env/bin/pip install -e apps/nasiya365 --no-deps

# Set working directory
WORKDIR /home/frappe/frappe-bench

# Expose port
EXPOSE 8000

# Default command
CMD ["sh", "-c", "cd sites && ../env/bin/gunicorn -b 0.0.0.0:8000 -w 4 -t 120 frappe.app:application --preload"]
