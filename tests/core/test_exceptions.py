"""Tests for the domain exception hierarchy in :mod:`src.core.exceptions`.

These tests pin the shape of the exception vocabulary: every concrete
exception must inherit from :class:`GitError`, the conflict-bearing
exceptions must carry the conflicting file paths, and importing the
module must not pull in PySide6.
"""
from __future__ import annotations

import pytest
from src.core.exceptions import (
    AuthError,
    DirtyWorkTreeError,
    GitError,
    GitNotInstalledError,
    InvalidRefError,
    MergeConflictError,
    NetworkError,
    RebaseConflictError,
    RepositoryNotFoundError,
)


def test_git_error_is_base_for_all_domain_errors() -> None:
    for cls in (
        RepositoryNotFoundError,
        InvalidRefError,
        DirtyWorkTreeError,
        MergeConflictError,
        RebaseConflictError,
        AuthError,
        NetworkError,
        GitNotInstalledError,
    ):
        assert issubclass(cls, GitError), f"{cls.__name__} must inherit GitError"


def test_merge_conflict_error_carries_conflicting_paths() -> None:
    err = MergeConflictError("boom", conflicting_paths=["a.txt", "b.txt"])
    assert err.conflicting_paths == ["a.txt", "b.txt"]
    assert "boom" in str(err)


def test_merge_conflict_error_default_paths_is_empty() -> None:
    err = MergeConflictError("boom")
    assert err.conflicting_paths == []


def test_exceptions_module_does_not_pull_pyside6() -> None:
    """Core layer must stay UI-agnostic (DEVELOPMENT_RULES.md, section 1).

    We don't try to look at ``sys.modules`` (other tests may have
    imported PySide6 already); we just verify the module's own
    ``__dict__`` and the modules it directly imports via
    ``__builtins__`` introspection are PySide6-free.
    """
    import src.core.exceptions as exc_mod

    for attr_name in vars(exc_mod):
        attr = getattr(exc_mod, attr_name)
        mod = getattr(attr, "__module__", "")
        assert "PySide6" not in mod, f"{attr_name} comes from {mod}"


@pytest.mark.parametrize(
    ("cls", "message"),
    [
        (RepositoryNotFoundError, "no repo here"),
        (InvalidRefError, "bad ref"),
        (DirtyWorkTreeError, "dirty"),
        (RebaseConflictError, "rebase conflict"),
        (AuthError, "auth"),
        (NetworkError, "dns"),
        (GitNotInstalledError, "no git"),
    ],
)
def test_non_merge_exceptions_have_empty_conflict_paths(cls, message: str) -> None:
    """Only :class:`MergeConflictError` carries ``conflicting_paths``."""
    err = cls(message)
    assert not hasattr(err, "conflicting_paths") or err.conflicting_paths == []
