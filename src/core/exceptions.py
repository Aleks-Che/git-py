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

    Attributes
    ----------
    conflicting_paths : list[str]
        Files that need attention.
    source_oid : str | None
        Full hex OID of the commit being merged in (second parent of
        the future merge commit). Stored as an OID rather than a ref
        name so a later fetch cannot change the parents of the
        in-progress merge.
    target_branch : str | None
        Short name of the branch being merged into (the ref that
        :func:`src.core.operations.complete_merge` must advance).
    target_oid : str | None
        Full hex OID of the target branch tip at merge start (first
        parent of the future merge commit).
    """

    def __init__(
        self,
        message: str,
        conflicting_paths: list[str] | None = None,
        *,
        source_oid: str | None = None,
        target_branch: str | None = None,
        target_oid: str | None = None,
    ) -> None:
        super().__init__(message)
        self.conflicting_paths = conflicting_paths or []
        self.source_oid = source_oid
        self.target_branch = target_branch
        self.target_oid = target_oid


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
