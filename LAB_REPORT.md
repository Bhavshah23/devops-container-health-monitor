# Mini Project Report

## Title

**Automated Container Health Monitoring and Real-Time Event Notifications via Webhooks and Discord API Integration**

## 1. Abstract

Modern applications commonly run as containers, but a running container is not necessarily a healthy application. This mini project implements an automated monitoring service that listens to Docker lifecycle events, periodically evaluates container health, records every important state change, and immediately posts a formatted alert to Discord using its Webhook API. An authenticated inbound webhook also allows external systems to use the same notification pipeline. A browser dashboard displays service readiness and recent events. The solution is packaged with Docker Compose and can be demonstrated using included healthy and intentionally unhealthy containers.

## 2. Problem statement

Manual inspection with `docker ps` and log commands is slow and unreliable. When a service becomes unhealthy, stops, is killed, or runs out of memory, a DevOps operator needs a fast notification and a persistent event trail. The project aims to automate detection and reduce the time between a failure and operator awareness.

## 3. Objectives

1. Monitor selected Docker containers continuously.
2. Detect health and lifecycle changes in near real time.
3. Deliver actionable notifications to a Discord channel.
4. Receive events from other tools through an authenticated webhook.
5. Store events for audit and troubleshooting.
6. Provide a simple dashboard and REST API.
7. Package the entire system for repeatable deployment.

## 4. Tools and technologies

| Technology | Role |
|---|---|
| Python 3.12 | Application logic |
| Flask + Gunicorn | Dashboard and REST/webhook API |
| Docker SDK for Python | Health queries and real-time event stream |
| Docker Compose | Multi-container deployment |
| SQLite | Persistent event history |
| Discord Webhook API | Real-time channel notifications |
| Requests | Outbound HTTPS integration |
| Pytest | Automated API testing |

## 5. System design

The system contains four logical parts:

1. **Docker collector:** consumes Docker's event stream and polls current health state.
2. **Event processor:** filters containers by label, detects transitions, and suppresses repeated alerts during a configurable cooldown.
3. **Notification adapter:** converts an event to a Discord embed and sends it through HTTPS.
4. **Storage and presentation:** saves the event to SQLite and exposes it on the API and dashboard.

### Event flow

1. A monitored container changes state.
2. Docker emits an event or the next poll observes the change.
3. The monitor verifies the opt-in label.
4. The event is normalized into container, type, status, time, and details.
5. The cooldown check prevents an alert storm.
6. The service posts a Discord embed and records whether delivery succeeded.
7. The dashboard retrieves recent records from the API every five seconds.

## 6. Implementation

The application is defined in `monitor.py`. A single Gunicorn worker hosts Flask while daemon threads handle event streaming and periodic polling. Configuration is supplied through environment variables. The SQLite volume retains history across restarts. Docker Compose builds the application and launches two test workloads. Only containers carrying `health-monitor.enabled=true` are processed.

The inbound webhook requires an `X-Webhook-Secret` header. Secret comparison uses a timing-safe operation. Incoming values are length-limited, messages cannot trigger Discord mentions, database writes use parameters, and dashboard text is HTML-escaped.

## 7. Test cases

| ID | Test | Expected result |
|---|---|---|
| T1 | Request `/health` | HTTP 200 and service `ok` |
| T2 | Insert and request an event | Latest event is returned from SQLite |
| T3 | Call webhook without correct secret | HTTP 401 |
| T4 | Call webhook with valid JSON and secret | HTTP 202 |
| T5 | Use a non-integer event limit | HTTP 400 |
| T6 | Allow unhealthy demo check to fail | Dashboard record and Discord alert |
| T7 | Stop the healthy demo | Stop event and Discord alert |

## 8. Expected result

After deployment, the dashboard reports the monitor online and Docker connected. The `healthy-demo` remains healthy. The `unhealthy-demo` fails its health check, causing a red Discord notification and a stored event. Stopping or starting a labelled container generates additional alerts. An authenticated sample webhook produces a purple external event notification.

## 9. Advantages

- Fast incident visibility without continuous manual checking
- Simple deployment and reproducible demonstration
- Low infrastructure requirements
- Extensible webhook-based design
- Persistent history even when Discord is unavailable
- Reduced notification noise through label filtering and cooldown

## 10. Limitations

- Designed for one Docker host rather than a distributed cluster
- Local dashboard endpoints do not include user authentication
- SQLite is not intended for large multi-node deployments
- Docker socket access is highly privileged
- In-memory cooldown state resets when the monitor restarts

## 11. Conclusion

The project demonstrates a practical DevOps feedback loop: observe infrastructure, detect a failure, persist evidence, and notify an operator automatically. It connects containerization, health checks, event-driven automation, REST webhooks, external API integration, secure configuration, testing, and operational documentation in a compact system suitable for a lab demonstration.

## 12. Viva questions and answers

**Why use a health check instead of only checking whether a container is running?**  
A process may still be running even when the application is unable to serve requests. A health check tests useful application behaviour.

**Why combine event streaming and polling?**  
Streaming gives low latency. Polling provides recovery if the event connection is interrupted or a state change is missed.

**What is a webhook?**  
A webhook is an HTTP callback that sends an event to a configured URL as soon as it occurs.

**Why is the Discord webhook URL secret?**  
Anyone possessing it can post messages to the associated Discord channel.

**What does the monitoring label do?**  
It creates an explicit allowlist so unrelated containers do not produce alerts.

**How is alert flooding controlled?**  
The service tracks the last matching container/status alert and suppresses repetitions during the cooldown interval.

**Why persist events when Discord already contains messages?**  
Local records support API queries and retain evidence of events even if notification delivery fails.

**What would change for Kubernetes?**  
The Docker collector would be replaced by Kubernetes watch APIs, and pod conditions plus liveness/readiness probe results would become the main signals.

