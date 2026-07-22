"""Shared pytest fixtures.

``tmp_git_repo`` mirrors the TEST_PLAN.md convention: every test that
needs a real repository gets a fresh, empty one built with
``pygit2.init_repository`` in pytest's ``tmp_path`` (auto-cleaned).

``committed_repo`` is a higher-level fixture: a repo with two commits
on ``main``, one with a tracked file (``hello.txt``). Most of the
``operations``/``repository`` tests use it instead of having to set up
their own commit each time.

``make_commit`` is a factory fixture for tests that need a specific
history shape (merge targets, conflict scenarios, ...).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pygit2
import pytest
from src.core.repository import RepositoryManager


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create and return the path to a fresh, empty Git repository."""
    repo_path = tmp_path / "repo"
    pygit2.init_repository(str(repo_path), initial_head="main")
    return repo_path


@pytest.fixture
def committed_repo(tmp_git_repo: Path) -> RepositoryManager:
    """A repo with two commits on ``main``; ``hello.txt`` is tracked."""
    manager = RepositoryManager(str(tmp_git_repo))
    sig = pygit2.Signature("tester", "tester@example.com", int(time.time()), 0)
    (tmp_git_repo / "hello.txt").write_text("hello\n")
    manager.repo.index.add("hello.txt")
    manager.repo.index.write()
    tree1 = manager.repo.index.write_tree()
    parents: list[pygit2.Oid] = []
    c1 = manager.repo.create_commit("refs/heads/main", sig, sig, "init: hello", tree1, parents)
    (tmp_git_repo / "hello.txt").write_text("hello, world\n")
    manager.repo.index.add("hello.txt")
    manager.repo.index.write()
    tree2 = manager.repo.index.write_tree()
    manager.repo.create_commit("refs/heads/main", sig, sig, "greet the world", tree2, [c1])
    return manager


@pytest.fixture
def make_commit(tmp_git_repo: Path) -> Callable[..., pygit2.Oid]:
    """Return a factory ``(message, files=None, parents=(), ref="refs/heads/main") -> Oid``.

    ``files`` is an optional ``{path: content}`` dict; when provided the
    contents are written to the worktree and staged before committing.
    ``parents`` is a list of parent OIDs (or commits); empty for a
    first commit. ``ref`` defaults to ``refs/heads/main``; pass something
    like ``refs/heads/feature`` to commit onto a different branch.
    """
    repo = pygit2.Repository(str(tmp_git_repo))
    sig = pygit2.Signature("tester", "tester@example.com", int(time.time()), 0)

    def _make(
        message: str,
        files: dict[str, str] | None = None,
        parents: list[pygit2.Oid] | None = None,
        ref: str = "refs/heads/main",
    ) -> pygit2.Oid:
        parents = parents if parents is not None else []
        parent_oids = [p if isinstance(p, pygit2.Oid) else pygit2.Oid(bytes(p)) for p in parents]
        if files:
            for path, content in files.items():
                full = tmp_git_repo / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content)
                repo.index.add(path)
            repo.index.write()
            tree_oid = repo.index.write_tree()
        else:
            builder = repo.TreeBuilder()
            if parents:
                # Inherit the tree from the first parent so subsequent
                # commits without explicit files don't look "empty".
                parent_tree = repo[parents[0]].tree
                builder = repo.TreeBuilder(parent_tree)
            tree_oid = builder.write()
        return repo.create_commit(ref, sig, sig, message, tree_oid, parent_oids)

    return _make
