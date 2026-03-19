"""
Task Status Routes
==================
GET  /api/tasks/{task_id}  — poll Celery task state + fetch result when done.

Used by the frontend ``useTaskStatus`` hook to track async optimizer/pipeline
jobs submitted via the ``/run-async`` endpoints.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from services.auth import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])


@router.get("/{task_id}")
async def get_task_status(
    task_id: str,
    current_user=Depends(get_current_user),  # noqa: ARG001 — auth only
) -> dict:
    """Return the current state of a background task.

    Possible ``status`` values:

    * ``queued``  — task is waiting in the queue
    * ``running`` — worker has started the task
    * ``done``    — task succeeded; ``result`` field contains the payload
    * ``error``   — task failed; ``error`` field contains the message
    """
    try:
        from backend.celery_app import app as celery_app

        ar = celery_app.AsyncResult(task_id)
        state = ar.state  # "PENDING" | "STARTED" | "SUCCESS" | "FAILURE" | "REVOKED"

        if state == "PENDING":
            return {"task_id": task_id, "status": "queued"}
        if state == "STARTED":
            return {"task_id": task_id, "status": "running"}
        if state == "SUCCESS":
            return {"task_id": task_id, "status": "done", "result": ar.result}
        if state == "FAILURE":
            return {"task_id": task_id, "status": "error", "error": str(ar.info)}
        # REVOKED or any other terminal state
        return {"task_id": task_id, "status": state.lower()}

    except ImportError:
        raise HTTPException(
            status_code=503,
            detail=(
                "Celery / Redis is not available. "
                "Start the redis + celery_worker Docker services or use the "
                "synchronous /run endpoint instead."
            ),
        )
    except Exception as exc:
        log.error("[tasks] Status check failed for %s: %s", task_id, exc)
        raise HTTPException(status_code=503, detail="Task broker unavailable")
