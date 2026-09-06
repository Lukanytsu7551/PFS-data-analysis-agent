"""Shared internal errors used to unwind bounded Agent work."""
from __future__ import annotations


class AgentRunTimeout(RuntimeError):
    """Internal signal for a provider or tool operation that reached the run deadline."""
