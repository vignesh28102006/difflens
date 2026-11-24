import json
import logging
import os
from typing import Any, Dict, Optional
import redis

logger = logging.getLogger("difflens.queue")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QUEUE_KEY = "difflens:pr_jobs"
DELIVERY_PREFIX = "difflens:delivery:"
DEFAULT_DELIVERY_TTL = 86400  # 24 hours


def get_redis_client(url: Optional[str] = None) -> redis.Redis:
    """Returns a Redis client instance."""
    redis_url = url or REDIS_URL
    return redis.from_url(redis_url, decode_responses=True)


def is_delivery_duplicate(
    redis_client: Any,
    delivery_id: str,
    ttl: int = DEFAULT_DELIVERY_TTL,
) -> bool:
    """
    Checks if a webhook delivery ID has already been seen using atomic SETNX.
    Returns:
        True if the delivery ID was already recorded (duplicate delivery).
        False if this is a new delivery (atomically stored with TTL).
    """
    if not delivery_id:
        return False

    key = f"{DELIVERY_PREFIX}{delivery_id}"
    try:
        # set with nx=True returns True if key was set, None/False if key already existed
        is_new = redis_client.set(key, "1", nx=True, ex=ttl)
        return not bool(is_new)
    except Exception as e:
        logger.warning("Redis delivery check failed, proceeding non-blocking: %s", e)
        return False


def enqueue_pr_job(redis_client: Any, payload: Dict[str, Any]) -> None:
    """Pushes a serialized PR analysis job onto the Redis queue."""
    serialized = json.dumps(payload)
    redis_client.rpush(QUEUE_KEY, serialized)
    logger.info("Enqueued PR job to %s: run_id=%s", QUEUE_KEY, payload.get("run_id"))


def pop_pr_job(redis_client: Any, timeout: int = 5) -> Optional[Dict[str, Any]]:
    """Pops a job from the Redis queue using blocking BLPOP."""
    item = redis_client.blpop(QUEUE_KEY, timeout=timeout)
    if not item:
        return None
    # blpop returns (queue_name, data)
    _, data = item
    try:
        return json.loads(data)
    except Exception as e:
        logger.error("Failed to deserialize job data: %s", e)
        return None
