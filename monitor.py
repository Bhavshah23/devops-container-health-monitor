"""Docker health monitor with Discord webhook notifications."""

from __future__ import annotations

import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
import requests
from docker.errors import DockerException
from flask import Flask, jsonify, render_template, request


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
)
logger = logging.getLogger("container-monitor")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Settings:
    def __init__(self) -> None:
        self.discord_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        self.webhook_secret = os.getenv("WEBHOOK_SECRET", "change-this-lab-secret")
        self.poll_interval = max(2, int(os.getenv("POLL_INTERVAL_SECONDS", "10")))
        self.cooldown = max(0, int(os.getenv("ALERT_COOLDOWN_SECONDS", "60")))
        self.monitor_label = os.getenv("MONITOR_LABEL", "health-monitor.enabled")
        self.db_path = os.getenv("DB_PATH", "/data/events.db")
        self.start_monitor = os.getenv("START_MONITOR", "true").lower() == "true"


class EventStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    container_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT NOT NULL,
                    notification_sent INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def add(
        self,
        container_name: str,
        event_type: str,
        status: str,
        details: dict[str, Any],
        notification_sent: bool,
    ) -> int:
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events
                    (occurred_at, container_name, event_type, status, details, notification_sent)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    container_name,
                    event_type,
                    status,
                    json.dumps(details, separators=(",", ":")),
                    int(notification_sent),
                ),
            )
            return int(cursor.lastrowid)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 200)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["details"] = json.loads(event["details"])
            event["notification_sent"] = bool(event["notification_sent"])
            events.append(event)
        return events


class DiscordNotifier:
    COLORS = {
        "healthy": 0x2ECC71,
        "unhealthy": 0xE74C3C,
        "die": 0xE67E22,
        "stop": 0xE67E22,
        "start": 0x3498DB,
        "external": 0x9B59B6,
        "test": 0x5865F2,
    }

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    @property
    def configured(self) -> bool:
        return self.webhook_url.startswith("https://discord.com/api/webhooks/")

    def send(self, container: str, event_type: str, status: str, details: dict[str, Any]) -> bool:
        if not self.configured:
            logger.warning("Discord webhook is not configured; alert stored locally only")
            return False

        fields = [
            {"name": "Container", "value": container[:1024], "inline": True},
            {"name": "Status", "value": status[:1024], "inline": True},
            {"name": "Event", "value": event_type[:1024], "inline": True},
        ]
        if details:
            detail_text = json.dumps(details, indent=2)[:1000]
            fields.append({"name": "Details", "value": f"```json\n{detail_text}\n```"})

        payload = {
            "username": "DevOps Health Monitor",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": "Container health notification",
                    "description": f"A **{status}** event was detected.",
                    "color": self.COLORS.get(status, 0x95A5A6),
                    "fields": fields,
                    "timestamp": utc_now(),
                    "footer": {"text": "Automated DevOps Lab Monitor"},
                }
            ],
        }
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=8)
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            logger.error("Discord notification failed: %s", exc)
            return False


class ContainerMonitor:
    IMPORTANT_ACTIONS = {"start", "stop", "die", "kill", "restart", "oom", "health_status"}

    def __init__(self, settings: Settings, store: EventStore, notifier: DiscordNotifier) -> None:
        self.settings = settings
        self.store = store
        self.notifier = notifier
        self.client: docker.DockerClient | None = None
        self.last_status: dict[str, str] = {}
        self.last_alert: dict[tuple[str, str], float] = {}
        self.started = False
        self.docker_connected = False

    def connect(self) -> bool:
        try:
            self.client = docker.from_env()
            self.client.ping()
            self.docker_connected = True
            return True
        except DockerException as exc:
            self.docker_connected = False
            logger.error("Cannot connect to Docker: %s", exc)
            return False

    def _is_monitored(self, container: Any) -> bool:
        return container.labels.get(self.settings.monitor_label, "false").lower() == "true"

    @staticmethod
    def _health(container: Any) -> str:
        state = container.attrs.get("State", {})
        health = state.get("Health", {}).get("Status")
        return health or state.get("Status", "unknown")

    def _within_cooldown(self, container: str, status: str) -> bool:
        key = (container, status)
        now = time.monotonic()
        last = self.last_alert.get(key, 0)
        if now - last < self.settings.cooldown:
            return True
        self.last_alert[key] = now
        return False

    def record_and_notify(
        self, container: str, event_type: str, status: str, details: dict[str, Any]
    ) -> None:
        should_notify = not self._within_cooldown(container, status)
        sent = should_notify and self.notifier.send(container, event_type, status, details)
        self.store.add(container, event_type, status, details, sent)
        logger.info("event=%s container=%s status=%s notified=%s", event_type, container, status, sent)

    def poll_once(self) -> None:
        if self.client is None and not self.connect():
            return
        try:
            assert self.client is not None
            containers = self.client.containers.list(all=True)
            self.docker_connected = True
            for container in containers:
                if not self._is_monitored(container):
                    continue
                container.reload()
                status = self._health(container)
                previous = self.last_status.get(container.id)
                self.last_status[container.id] = status
                if previous is None and status in {"unhealthy", "exited", "dead"}:
                    self.record_and_notify(
                        container.name,
                        "initial_health_check",
                        status,
                        {"previous_status": "unknown", "container_id": container.short_id},
                    )
                elif previous is not None and status != previous:
                    self.record_and_notify(
                        container.name,
                        "health_change",
                        status,
                        {"previous_status": previous, "container_id": container.short_id},
                    )
        except DockerException as exc:
            self.docker_connected = False
            self.client = None
            logger.error("Docker polling error: %s", exc)

    def polling_loop(self) -> None:
        while True:
            self.poll_once()
            time.sleep(self.settings.poll_interval)

    def docker_event_loop(self) -> None:
        while True:
            if self.client is None and not self.connect():
                time.sleep(5)
                continue
            try:
                assert self.client is not None
                for event in self.client.events(decode=True, filters={"type": "container"}):
                    action = event.get("Action", "unknown")
                    base_action = action.split(":", 1)[0]
                    if base_action not in self.IMPORTANT_ACTIONS:
                        continue
                    attributes = event.get("Actor", {}).get("Attributes", {})
                    if attributes.get(self.settings.monitor_label, "false").lower() != "true":
                        continue
                    name = attributes.get("name", "unknown")
                    status = action.split(":", 1)[1].strip() if ":" in action else base_action
                    self.record_and_notify(
                        name,
                        "docker_event",
                        status,
                        {"action": action, "container_id": event.get("id", "")[:12]},
                    )
            except (DockerException, requests.RequestException) as exc:
                self.docker_connected = False
                self.client = None
                logger.error("Docker event stream error: %s", exc)
                time.sleep(5)

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        threading.Thread(target=self.polling_loop, name="health-poller", daemon=True).start()
        threading.Thread(target=self.docker_event_loop, name="docker-events", daemon=True).start()


settings = Settings()
store = EventStore(settings.db_path)
notifier = DiscordNotifier(settings.discord_url)
monitor = ContainerMonitor(settings, store, notifier)
app = Flask(__name__)


@app.get("/")
def dashboard() -> str:
    return render_template("index.html")


@app.get("/health")
def health() -> tuple[Any, int]:
    return jsonify(
        {
            "service": "ok",
            "docker_connected": monitor.docker_connected,
            "discord_configured": notifier.configured,
            "time": utc_now(),
        }
    ), 200


@app.get("/api/events")
def events() -> Any:
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify(store.recent(limit))


@app.post("/api/test-alert")
def test_alert() -> tuple[Any, int]:
    secret = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(secret, settings.webhook_secret):
        return jsonify({"error": "unauthorized"}), 401
    sent = notifier.send("manual-test", "test", "test", {"source": "dashboard/API"})
    store.add("manual-test", "test", "test", {"source": "dashboard/API"}, sent)
    return jsonify({"accepted": True, "discord_sent": sent}), 202


@app.post("/webhook/events")
def inbound_webhook() -> tuple[Any, int]:
    secret = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(secret, settings.webhook_secret):
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required"}), 400
    container = str(payload.get("container", "external-service"))[:100]
    status = str(payload.get("status", "external"))[:40]
    details = payload.get("details", {})
    if not isinstance(details, dict):
        details = {"message": str(details)}
    monitor.record_and_notify(container, "external_webhook", status, details)
    return jsonify({"accepted": True}), 202


@app.errorhandler(404)
def not_found(_: Any) -> tuple[Any, int]:
    return jsonify({"error": "not found"}), 404


if settings.start_monitor:
    monitor.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
