import { Output, randomPassword, Services } from "~templates-utils";
import { Input } from "./meta";

export function generate(input: Input): Output {
    const services: Services = [];
    const databasePassword = input.databasePassword || randomPassword();
    const adminPassword = input.adminPassword || randomPassword();

    // MariaDB Database
    services.push({
        type: "mariadb",
        data: {
            serviceName: input.databaseServiceName,
            password: databasePassword,
            image: "mariadb:10.6",
        },
    });

    // Redis Cache
    services.push({
        type: "redis",
        data: {
            serviceName: `${input.redisServiceName}-cache`,
            image: "redis:alpine",
        },
    });

    // Redis Queue
    services.push({
        type: "redis",
        data: {
            serviceName: `${input.redisServiceName}-queue`,
            image: "redis:alpine",
        },
    });

    // Backend Service
    services.push({
        type: "app",
        data: {
            serviceName: `${input.appServiceName}-backend`,
            source: {
                type: "github",
                owner: "alraqam",
                repo: "nasiya365-frappe",
                ref: "main",
                path: "/",
            },
            build: {
                type: "dockerfile",
                file: "Dockerfile",
            },
            deploy: {
                command: `sh -c "cd sites && ../env/bin/gunicorn -b 0.0.0.0:8000 -w 4 -t 120 frappe.app:application --preload"`,
            },
            mounts: [
                {
                    type: "volume",
                    name: "sites",
                    mountPath: "/home/frappe/frappe-bench/sites",
                },
                {
                    type: "volume",
                    name: "logs",
                    mountPath: "/home/frappe/frappe-bench/logs",
                },
            ],
            env: [
                `FRAPPE_SITE_NAME_HEADER=$(PRIMARY_DOMAIN)`,
                `DB_HOST=$(PROJECT_NAME)_${input.databaseServiceName}`,
                `DB_PASSWORD=${databasePassword}`,
                `REDIS_CACHE=$(PROJECT_NAME)_${input.redisServiceName}-cache:6379`,
                `REDIS_QUEUE=$(PROJECT_NAME)_${input.redisServiceName}-queue:6379`,
            ].join("\n"),
        },
    });

    // Frontend (Nginx) Service
    services.push({
        type: "app",
        data: {
            serviceName: `${input.appServiceName}-frontend`,
            source: {
                type: "image",
                image: "frappe/erpnext:v16",
            },
            deploy: {
                command: "nginx-entrypoint.sh",
            },
            domains: [
                {
                    host: "$(EASYPANEL_DOMAIN)",
                    port: 8080,
                },
            ],
            mounts: [
                {
                    type: "volume",
                    name: "sites",
                    mountPath: "/home/frappe/frappe-bench/sites",
                },
                {
                    type: "volume",
                    name: "logs",
                    mountPath: "/home/frappe/frappe-bench/logs",
                },
            ],
            env: [
                `BACKEND=$(PROJECT_NAME)_${input.appServiceName}-backend:8000`,
                `FRAPPE_SITE_NAME_HEADER=$(PRIMARY_DOMAIN)`,
                `SOCKETIO=$(PROJECT_NAME)_${input.appServiceName}-websocket:9000`,
                `UPSTREAM_REAL_IP_ADDRESS=127.0.0.1`,
                `UPSTREAM_REAL_IP_HEADER=X-Forwarded-For`,
                `CLIENT_MAX_BODY_SIZE=50m`,
            ].join("\n"),
        },
    });

    // WebSocket Service
    services.push({
        type: "app",
        data: {
            serviceName: `${input.appServiceName}-websocket`,
            source: {
                type: "image",
                image: "frappe/erpnext:v16",
            },
            deploy: {
                command: "node /home/frappe/frappe-bench/apps/frappe/socketio.js",
            },
            mounts: [
                {
                    type: "volume",
                    name: "sites",
                    mountPath: "/home/frappe/frappe-bench/sites",
                },
            ],
            env: [
                `FRAPPE_SITE_NAME_HEADER=$(PRIMARY_DOMAIN)`,
            ].join("\n"),
        },
    });

    // Worker Default Queue
    services.push({
        type: "app",
        data: {
            serviceName: `${input.appServiceName}-worker`,
            source: {
                type: "github",
                owner: "alraqam",
                repo: "nasiya365-frappe",
                ref: "main",
                path: "/",
            },
            build: {
                type: "dockerfile",
                file: "Dockerfile",
            },
            deploy: {
                command: "bench worker --queue default",
            },
            mounts: [
                {
                    type: "volume",
                    name: "sites",
                    mountPath: "/home/frappe/frappe-bench/sites",
                },
                {
                    type: "volume",
                    name: "logs",
                    mountPath: "/home/frappe/frappe-bench/logs",
                },
            ],
        },
    });

    // Scheduler
    services.push({
        type: "app",
        data: {
            serviceName: `${input.appServiceName}-scheduler`,
            source: {
                type: "github",
                owner: "alraqam",
                repo: "nasiya365-frappe",
                ref: "main",
                path: "/",
            },
            build: {
                type: "dockerfile",
                file: "Dockerfile",
            },
            deploy: {
                command: "bench schedule",
            },
            mounts: [
                {
                    type: "volume",
                    name: "sites",
                    mountPath: "/home/frappe/frappe-bench/sites",
                },
            ],
        },
    });

    return { services };
}
