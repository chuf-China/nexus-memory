"""nexus_exceptions.py — Unified error handling for Nexus.

All public APIs raise NexusError subclasses.
safe_execute() wraps calls with try/except → log + fallback.

Usage:
  from .exceptions import safe_execute, StorageError
  result = safe_execute(lambda: risky_operation(), fallback=None)
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class NexusError(Exception):
    """Base exception for all Nexus errors."""


class StorageError(NexusError):
    """Database read/write errors."""


class ExtractionError(NexusError):
    """Entity/fact extraction errors."""


class ResolutionError(NexusError):
    """Entity resolution errors."""


class RetrievalError(NexusError):
    """Search/retrieval errors."""


class GraphError(NexusError):
    """Knowledge graph errors."""


class ConfigError(NexusError):
    """Configuration errors."""


def safe_execute(fn: Callable[..., T],
                 fallback: Optional[Any] = None,
                 log_level: str = "debug",
                 reraise: bool = False) -> Any:
    """Execute fn() safely. On error: log + return fallback.

    Args:
        fn: Callable to execute.
        fallback: Value to return on error.
        log_level: "debug", "info", "warning", "error".
        reraise: If True, re-raise after logging.

    Returns:
        fn() result or fallback on error.
    """
    try:
        return fn()
    except NexusError as e:
        getattr(logger, log_level)("Nexus error: %s", e)
        if reraise:
            raise
        return fallback
    except Exception as e:
        getattr(logger, log_level)("Nexus unexpected error: %s", e, exc_info=True)
        if reraise:
            raise NexusError(str(e)) from e
        return fallback


def safe_write(fn: Callable[..., T], fallback: Optional[Any] = None) -> Any:
    """Safe execute for write operations (logs at warning level)."""
    return safe_execute(fn, fallback=fallback, log_level="warning")


def safe_read(fn: Callable[..., T], fallback: Optional[Any] = None) -> Any:
    """Safe execute for read operations (logs at debug level)."""
    return safe_execute(fn, fallback=fallback, log_level="debug")
