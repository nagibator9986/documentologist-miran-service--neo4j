"""Shared slowapi rate limiter instance."""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# Keyed by client IP; uses in-memory storage by default (suitable for single-process).
# For multi-process/multi-replica deployments, swap storage_uri to Redis:
#   from .config import get_settings
#   limiter = Limiter(key_func=get_remote_address, storage_uri=get_settings().redis_url)
limiter = Limiter(key_func=get_remote_address)
