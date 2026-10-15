# Cloud-Monitoring

A small uptime monitor that runs on **free infrastructure** (GitHub Actions cron, or any local cron/laptop).
It pings your endpoints, records **latency in milliseconds, HTTP status codes and uptime %**, sends an
**alert the moment a threshold is breached** (console, Slack/Discord/Teams webhook, or e-mail), and publishes a
**historical availability dashboard**.
