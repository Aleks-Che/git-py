"""Domain exceptions raised by the core layer.

Per ``docs/DEVELOPMENT_RULES.md`` (section 4), every ``pygit2.GitError``
raised inside ``core/`` MUST be wrapped in one of these domain types
before bubbling up to a ViewModel. ViewModels then surface the message
via the ``error_occurred`` signal — raw Python exceptions never reach
the UI.

The hierarchy mirrors the failure modes the UI cares about, so the
view-model can choose the right reaction (e.g. open the conflict
resolver on ``MergeConflictError``, prompt for credentials on
``AuthError``).
"""
from __future__ import annotations


class GitError(Exception):
    """Base class for all domain errors raised by ``core/``."""


class RepositoryNotFoundError(GitError):
    """The given path does not contain a Git repository (or does not exist)."""


class InvalidRefError(GitError):
    """A reference name is malformed or points to a non-existent object."""


class DirtyWorkTreeError(GitError):
    """The operation requires a clean worktree but uncommitted changes were found."""


class MergeConflictError(GitError):
    """A merge produced index conflicts that must be resolved before continuing.

    The ``conflicting_paths`` attribute (when set) lists the files that
    need attention.
    """

    def __init__(self, message: str, conflicting_paths: list[str] | None = None) -> None:
        super().__init__(message)
        self.conflicting_paths = conflicting_paths or []


class RebaseConflictError(GitError):
    """A rebase stopped mid-flight due to conflicts (cherry-pick step failed)."""


class AuthError(GitError):
    """Authentication failed for a remote operation (push/pull/fetch/clone)."""


class NetworkError(GitError):
    """Network-level failure during push/pull/fetch/clone (DNS, TLS, timeout, ...)."""


class GitNotInstalledError(GitError):
    """An operation shell out to the ``git`` CLI but it is not in PATH."""


__all__ = [
    "AuthError",
    "DirtyWorkTreeError",
    "GitError",
    "GitNotInstalledError",
    "InvalidRefError",
    "MergeConflictError",
    "NetworkError",
    "RebaseConflictError",
    "RepositoryNotFoundError",
]
