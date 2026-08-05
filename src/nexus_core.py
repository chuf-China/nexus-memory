"""
Nexus core — Unified Knowledge Store.

NexusCore is a facade that delegates to focused mixin modules:
  - nexus_core_db        — DB connection, backup, integrity
  - nexus_core_session   — Session resolution
  - nexus_core_audit     — Temporal, constitution, audit
  - nexus_core_snapshot  — Point-in-time snapshots
  - nexus_core_write     — Write, belief, feedback, promotion
  - nexus_core_search_ext — Search with domain scoring
  - nexus_core_stats     — Stats, system prompt, FTS, consolidation

All public APIs are preserved for backward compatibility.
"""

from __future__ import annotations

from .nexus_core_db import DbMixin
from .nexus_core_session import SessionMixin
from .nexus_core_audit import AuditMixin
from .nexus_core_snapshot import SnapshotMixin
from .nexus_core_write import WriteMixin
from .nexus_core_search_ext import SearchExtMixin
from .nexus_core_stats import StatsMixin


class NexusCore(
    DbMixin,
    SessionMixin,
    AuditMixin,
    SnapshotMixin,
    WriteMixin,
    SearchExtMixin,
    StatsMixin,
):
    """Unified facade — all methods available via single import.

    Usage:
        from src.nexus_core import NexusCore
        nexus = NexusCore("nexus.db")
        nexus.write("fact", source_session_id="conversation")
        results = nexus.search("query")
    """
    pass
