"""
Celery Application
==================
Shared Celery app instance.  Import this in tasks and the task-status router.

Broker + backend default to Redis on the internal Docker network.
Override via environment variables for local dev or managed Redis:

    CELERY_BROKER_URL=redis://localhost:6379/0
    CELERY_RESULT_BACKEND=redis://localhost:6379/0
"""

from __future__ import annotations

import os

from celery import Celery

app = Celery(
    "dfs_edge",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
    include=["backend.tasks.optimizer"],
)

app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=3600,        # task results live in Redis for 1 hour
    worker_prefetch_multiplier=1,   # one task at a time per worker slot (CPU-bound)
    task_acks_late=True,            # ack after task completes (safe for retries)
    worker_max_tasks_per_child=50,  # recycle worker processes to avoid memory leaks
)
