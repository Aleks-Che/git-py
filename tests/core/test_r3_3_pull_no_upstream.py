"""Regression tests for pull behavior without an upstream branch."""
from __future__ import annotations

from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import GitError
from src.core.operations import pull
from src.core.repository import RepositoryManager


def test_pull_without_upstream_raises_git_error(tmp_git_repo: Path) -> None:
    """A local-only branch reports how to configure upstream tracking."""
    manager = RepositoryManager(str(tmp_git_repo))
    repo = manager.repo
    signature = pygit2.Signature("tester", "tester@example.com")
    tree = repo.TreeBuilder().write()
    repo.create_commit("refs/heads/local-only", signature, signature, "initial", tree, [])
    repo.set_head("refs/heads/local-only")
    repo.checkout_head()

    with pytest.raises(GitError, match=r"No upstream branch configured for local-only") as exc_info:
        pull(manager)

    assert "git branch --set-upstream-to" in str(exc_info.value)
    assert "configure remote tracking" in str(exc_info.value)
