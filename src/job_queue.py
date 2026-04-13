"""
job_queue.py — rq (Redis Queue) setup.

Provides a single shared Queue instance used by both api.py (to enqueue)
and the rq worker process (to dequeue and execute).
"""

import sys
from pathlib import Path

from redis import Redis
from rq import Queue

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings


def get_redis() -> Redis:
    return Redis.from_url(settings.REDIS_URL)


def get_queue() -> Queue:
    return Queue("default", connection=get_redis())
