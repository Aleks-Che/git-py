"""Central ``MainViewModel`` dispatching UI state to the panels.

Stage 0 stub. Will eventually own the :class:`RepositoryManager`, the
:class:`CommandProcessor`, and expose high-level verb methods
(``commit_changes()``, ``checkout_branch(name)``, ``merge_branch(src, tgt)``)
for the main window and panels to call.
"""
from __future__ import annotations


class MainViewModel:
    """Placeholder for the Stage 3+ central ViewModel."""

    def __repr__(self) -> str:
        return "MainViewModel(<stub>)"
