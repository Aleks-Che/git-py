"""Domain data structures for Git objects (commit, branch, tag, file status, ...).

These dataclasses are intentionally decoupled from pygit2 objects: Core
returns copies of them so the ViewModel and UI never hold references to
live libgit2 state. The `serialisable` shape also keeps them cheap to
send across thread boundaries (see utils/async_worker.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FileStatus(str, Enum):
    """Working-tree and index file status."""

    NEW = "new"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    UNTRACKED = "untracked"
    IGNORED = "ignored"
    CONFLICTED = "conflicted"
    TYPE_CHANGED = "type_changed"


@dataclass
class CommitInfo:
    sha: str
    short_sha: str
    message: str
    author_name: str
    author_email: str
    author_time: int
    committer_name: str
    committer_email: str
    committer_time: int
    parents: list[str] = field(default_factory=list)
    kind: str = "commit"  # "commit" | "wip" | "stash" — graph widget only


@dataclass
class BranchInfo:
    name: str
    is_head: bool = False
    is_remote: bool = False
    upstream: str | None = None
    target_sha: str | None = None


@dataclass
class TagInfo:
    name: str
    target_sha: str
    is_annotated: bool = False
    message: str | None = None
    tagger_name: str | None = None
    tagger_email: str | None = None


@dataclass
class DiffEntry:
    old_path: str | None
    new_path: str | None
    status: FileStatus
    additions: int = 0
    deletions: int = 0
    is_binary: bool = False
    raw_patch: str | None = None


@dataclass
class StashInfo:
    index: int
    message: str
    sha: str
    branch: str | None = None


@dataclass
class FileChange:
    """A single working-tree or index status entry.

    Returned by :meth:`RepositoryManager.get_status`. ``path`` is the path
    relative to the repository workdir (using forward slashes); ``status``
    is the resolved status. If a file is both staged and modified in the
    worktree, the staged (index) status wins — matching ``git status``.
    """

    path: str
    status: FileStatus


@dataclass
class RemoteInfo:
    """A single Git remote (``origin``, ``upstream``, ...).

    Returned by :func:`src.core.operations.list_remotes` and surfaced
    to the UI through :class:`src.viewmodels.branch_panel_viewmodel.BranchPanelViewModel`.
    ``fetch_refspec`` and ``push_refspec`` default to ``"+" + name + "/*"``
    (the libgit2 default) when the remote has no explicit refspecs.
    """

    name: str
    url: str
    fetch_refspec: str = ""
    push_refspec: str = ""
