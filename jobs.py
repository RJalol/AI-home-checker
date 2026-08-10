"""
jobs.py
Thin wrapper around Redis + RQ for the async job queue behind
POST /jobs and GET /jobs/{job_id} in api.py.

Why a queue instead of grading inside the HTTP request (like /analyze
does): when many students submit at once -- e.g. from a CRM -- handling
each one synchronously means the caller's HTTP connection sits open for
however long Gemini takes (often 10-30+ seconds), which risks CRM
timeouts, and many requests arriving together can hit Gemini's own rate
limits. With a queue, POST /jobs returns in milliseconds (it just records
the job and returns an id); one or more separate `rq worker` processes
pull jobs off the queue and grade them one at a time (or several in
parallel if you run several workers), retrying/backing off as needed
without ever blocking the API itself.
"""

import redis
from rq import Queue
from rq.job import Job

from config import JOB_QUEUE_NAME, JOB_RESULT_TTL_SECONDS, JOB_TIMEOUT_SECONDS, REDIS_URL

_redis_conn = None
_queue = None


def get_redis_connection():
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = redis.from_url(REDIS_URL)
    return _redis_conn


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue(JOB_QUEUE_NAME, connection=get_redis_connection())
    return _queue


def enqueue_grading_job(**task_kwargs) -> str:
    """
    Enqueues one grading job and returns its job_id immediately (does not
    wait for grading to finish). task_kwargs are passed straight through
    to worker_tasks.grade_homework_task.
    """
    from worker_tasks import grade_homework_task

    job = get_queue().enqueue(
        grade_homework_task,
        kwargs=task_kwargs,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=JOB_RESULT_TTL_SECONDS,
        failure_ttl=JOB_RESULT_TTL_SECONDS,
    )
    return job.id


def get_job_status(job_id: str) -> dict:
    """
    Returns {"job_id", "status", ...}. status is one of:
    "queued", "started", "finished", "failed", "not_found".
    "finished" responses include "result" (the grading JSON);
    "failed" responses include a short "error" message.
    """
    conn = get_redis_connection()
    try:
        job = Job.fetch(job_id, connection=conn)
    except Exception:
        return {"job_id": job_id, "status": "not_found"}

    status = job.get_status(refresh=True)
    response = {"job_id": job_id, "status": status}

    if status == "finished":
        response["result"] = job.result
    elif status == "failed":
        exc_text = job.exc_info or ""
        last_line = exc_text.strip().splitlines()[-1] if exc_text.strip() else "Unknown error"
        response["error"] = last_line

    return response
