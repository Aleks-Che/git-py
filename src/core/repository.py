"""RepositoryManager: open, init, clone, and inspect a Git repository.

The manager is the only object in the Core layer that holds a
``pygit2.Repository``. All higher layers (ViewModels, widgets) talk to
it through the typed properties and dataclass return values defined
here; they never touch ``pygit2`` directly. Every public method
translates ``pygit2`` exceptions into domain exceptions from
:mod:`src.core.exceptions` so the UI sees a single, narrow failure
vocabulary.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pygit2

from src.core.exceptions import (
    GitError,
    InvalidRefError,
    RepositoryNotFoundError,
)
from src.core.models import (
    BranchInfo,
    CommitInfo,
    FileChange,
    FileStatus,
    StashInfo,
    TagInfo,
)


@contextlib.contextmanager
def unwrap(repo_or_manager: RepositoryManager | pygit2.Repository) -> Iterator[pygit2.Repository]:
    """Accept either a :class:`RepositoryManager` or a raw ``pygit2.Repository``."""
    if isinstance(repo_or_manager, RepositoryManager):
        yield repo_or_manager.repo
    else:
        yield repo_or_manager


class RepositoryManager:
    """Facade over :class:`pygit2.Repository` with typed, exception-safe APIs."""

    def __init__(self, path: str | None = None) -> None:
        self._path: str | None = None
        self._repo: pygit2.Repository | None = None
        if path is not None:
            self.open(path)

    # ----- accessors ---------------------------------------------------

    @property
    def path(self) -> str | None:
        """Filesystem path of the currently open repository, or ``None``."""
        return self._path

    @property
    def repo(self) -> pygit2.Repository:
        """Underlying ``pygit2.Repository``. Raises if no repo is open."""
        if self._repo is None:
            raise GitError("No repository is open. Call open() or init() first.")
        return self._repo

    @property
    def is_open(self) -> bool:
        return self._repo is not None

    @property
    def is_bare(self) -> bool:
        return self._repo is not None and self._repo.is_bare

    # ----- lifecycle ---------------------------------------------------

    def open(self, path: str) -> None:
        """Open an existing repository at ``path``."""
        p = Path(path)
        if not p.exists():
            raise RepositoryNotFoundError(f"Path does not exist: {path}")
        try:
            self._repo = pygit2.Repository(str(p))
        except pygit2.GitError as exc:
            raise RepositoryNotFoundError(f"Not a Git repository: {path}") from exc
        self._path = str(p)

    def init(
        self,
        path: str,
        initial_head: str = "main",
        bare: bool = False,
    ) -> None:
        """Create a new repository at ``path`` and open it.

        The default is an empty repo (no initial commit); ``HEAD`` is set
        up as an unborn reference pointing at ``initial_head``. Callers
        that need a starting commit should make one explicitly.
        """
        try:
            self._repo = pygit2.init_repository(path, bare=bare, initial_head=initial_head)
        except pygit2.GitError as exc:
            raise GitError(f"Failed to initialize repository at {path}: {exc}") from exc
        self._path = str(Path(path))

    def clone(
        self,
        url: str,
        path: str,
        bare: bool = False,
        callbacks: pygit2.RemoteCallbacks | None = None,
    ) -> None:
        """Clone ``url`` to ``path``.

        Synchronous for now; Stage 6 will wrap this in
        :class:`src.utils.async_worker.AsyncWorker`. Authenticated
        remotes require a ``callbacks`` object — without it, a
        :class:`src.core.exceptions.AuthError` is raised on the first
        credential prompt.
        """
        try:
            self._repo = pygit2.clone_repository(url, path, bare=bare, callbacks=callbacks)
        except pygit2.GitError as exc:
            self._repo = None
            self._path = None
            raise GitError(f"Failed to clone {url} -> {path}: {exc}") from exc
        self._path = str(Path(path))

    def close(self) -> None:
        """Drop the underlying ``pygit2.Repository`` (the on-disk repo is untouched)."""
        self._repo = None
        self._path = None

    @staticmethod
    def is_valid(path: str) -> bool:
        """Return ``True`` if ``path`` looks like a Git repository (regular or bare)."""
        p = Path(path)
        if not p.exists():
            return False
        if (p / ".git").exists():
            return True
        # Bare repo: HEAD + objects + refs at the root.
        if (p / "HEAD").exists() and (p / "objects").exists() and (p / "refs").exists():
            return True
        return False

    # ----- queries (properties) ----------------------------------------

    @property
    def head_commit(self) -> CommitInfo:
        """The commit ``HEAD`` points at. Raises if HEAD is unborn."""
        if self.repo.head_is_unborn:
            raise GitError("HEAD is unborn (repository has no commits yet).")
        try:
            commit = self.repo[self.repo.head.target]
        except (KeyError, pygit2.GitError) as exc:
            raise GitError(f"Cannot resolve HEAD: {exc}") from exc
        return self._to_commit_info(commit)

    @property
    def branches(self) -> list[BranchInfo]:
        """All local and remote-tracking branches known to the repository."""
        result: list[BranchInfo] = []
        head_name = self.repo.head.shorthand if not self.repo.head_is_unborn else None
        for name in self.repo.branches.local:
            branch = self.repo.lookup_branch(name)
            result.append(
                BranchInfo(
                    name=name,
                    is_head=(name == head_name),
                    is_remote=False,
                    target_sha=str(branch.target),
                ),
            )
        for name in self.repo.branches.remote:
            branch = self.repo.lookup_branch(name, pygit2.enums.BranchType.REMOTE)
            # ``upstream_name`` is a local-branch-only property; remote
            # branches raise ``ValueError`` if you call it. The remote
            # branch's own name (e.g. "origin/main") is the most useful
            # upstream identifier we have.
            try:
                upstream: str | None = branch.upstream_name
            except (ValueError, AttributeError):
                upstream = name
            result.append(
                BranchInfo(
                    name=name,
                    is_head=False,
                    is_remote=True,
                    upstream=upstream,
                    target_sha=str(branch.target),
                ),
            )
        return result

    @property
    def tags(self) -> list[TagInfo]:
        """All tags (annotated and lightweight)."""
        result: list[TagInfo] = []
        for ref_name in self.repo.references:
            if not ref_name.startswith("refs/tags/"):
                continue
            name = ref_name[len("refs/tags/"):]
            ref = self.repo.lookup_reference(ref_name)
            # ``ref.peel()`` would skip straight to the target commit,
            # losing the annotated-tag object. Look up the ref's direct
            # target instead: it points at the tag object for annotated
            # tags and at the commit for lightweight ones.
            obj = self.repo[ref.target]
            if obj.type == pygit2.GIT_OBJECT_TAG:
                tag: pygit2.Tag = obj
                tagger = tag.tagger
                target = tag.target
                target_sha = str(target.id if hasattr(target, "id") else target)
                result.append(
                    TagInfo(
                        name=name,
                        target_sha=target_sha,
                        is_annotated=True,
                        message=tag.message,
                        tagger_name=tagger.name if tagger else None,
                        tagger_email=tagger.email if tagger else None,
                    ),
                )
            else:
                result.append(
                    TagInfo(
                        name=name,
                        target_sha=str(obj.id),
                        is_annotated=False,
                    ),
                )
        return result

    @property
    def stash_list(self) -> list[StashInfo]:
        """All stash entries, most recent first (index 0 is ``stash@{0}``)."""
        result: list[StashInfo] = []
        for idx, entry in enumerate(self.repo.listall_stashes()):
            sha = entry.commit_id if hasattr(entry, "commit_id") else entry
            result.append(StashInfo(index=idx, message=entry.message.strip(), sha=str(sha)))
        return result

    # ----- queries (methods) -------------------------------------------

    def get_status(self) -> list[FileChange]:
        """Working-tree and index status as a list of :class:`FileChange`.

        When a file is both staged and modified in the worktree, the
        staged (index) status is reported — matching ``git status``.
        """
        result: list[FileChange] = []
        for path, flag in self.repo.status().items():
            result.append(FileChange(path=path, status=self._map_status(flag)))
        return result

    def get_history(
        self,
        branch: str | None = None,
        max_count: int = 100,
    ) -> list[CommitInfo]:
        """Walk commit history, newest first.

        ``branch`` is a local branch name; ``None`` means ``HEAD``.
        Returns an empty list if the repository has no commits.
        """
        if max_count <= 0:
            return []
        if branch is None:
            if self.repo.head_is_unborn:
                return []
            start = self.repo.head.target
        else:
            try:
                looked_up = self.repo.lookup_branch(branch)
            except (KeyError, ValueError) as exc:
                raise InvalidRefError(f"Unknown branch: {branch!r}") from exc
            if looked_up is None:
                raise InvalidRefError(f"Unknown branch: {branch!r}")
            start = looked_up.target
        result: list[CommitInfo] = []
        for commit in self.repo.walk(start, pygit2.GIT_SORT_TIME):
            if len(result) >= max_count:
                break
            result.append(self._to_commit_info(commit))
        return result

    def get_all_history(self, max_count: int = 500) -> list[CommitInfo]:
        """Walk the full commit DAG reachable from any local branch or tag.

        Used by the graph view, which needs every commit visible in the
        repository (not just the chain under ``HEAD``). We collect the
        tip OIDs of every local branch and every tag, walk each one
        with :data:`pygit2.GIT_SORT_TIME`, deduplicate by SHA, then
        re-sort the merged set by commit time, newest first.

        Returns an empty list if the repository has no commits.
        ``max_count`` caps the total; we stop as soon as it is reached
        (no fancy top-K across walks).
        """
        if max_count <= 0 or self.repo.head_is_unborn:
            return []
        tip_oids: set[pygit2.Oid] = set()
        for name in self.repo.branches.local:
            branch = self.repo.lookup_branch(name)
            if branch.target is not None:
                tip_oids.add(branch.target)
        for ref_name in self.repo.references:
            if not ref_name.startswith("refs/tags/"):
                continue
            ref = self.repo.lookup_reference(ref_name)
            tip_oids.add(ref.target)
        seen: set[str] = set()
        collected: list[pygit2.Commit] = []
        for tip in tip_oids:
            for commit in self.repo.walk(tip, pygit2.GIT_SORT_TIME):
                sha = str(commit.id)
                if sha in seen:
                    continue
                seen.add(sha)
                collected.append(commit)
                if len(collected) >= max_count:
                    break
            if len(collected) >= max_count:
                break
        collected.sort(key=lambda c: -c.commit_time)
        return [self._to_commit_info(c) for c in collected]

    def get_commit(self, sha: str) -> CommitInfo:
        """Resolve any revision (``HEAD``, branch name, short SHA, full SHA) to a commit."""
        try:
            obj = self.repo.revparse_single(sha).peel(pygit2.Commit)
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise InvalidRefError(f"Unknown revision: {sha!r}") from exc
        return self._to_commit_info(obj)

    # ----- internals ---------------------------------------------------

    @staticmethod
    def _to_commit_info(commit: pygit2.Commit) -> CommitInfo:
        author = commit.author
        committer = commit.committer
        return CommitInfo(
            sha=str(commit.id),
            short_sha=str(commit.short_id),
            message=commit.message,
            author_name=author.name,
            author_email=author.email,
            author_time=author.time,
            committer_name=committer.name,
            committer_email=committer.email,
            committer_time=committer.time,
            parents=[str(p) for p in commit.parent_ids],
        )

    @staticmethod
    def _map_status(flag: int) -> FileStatus:
        """Translate a pygit2 status bitfield into a :class:`FileStatus`."""
        if flag & pygit2.GIT_STATUS_CONFLICTED:
            return FileStatus.CONFLICTED
        if flag & pygit2.GIT_STATUS_IGNORED:
            return FileStatus.IGNORED
        # Index flags take priority over worktree flags (matches ``git status``).
        if flag & pygit2.GIT_STATUS_INDEX_NEW:
            return FileStatus.NEW
        if flag & pygit2.GIT_STATUS_INDEX_RENAMED:
            return FileStatus.RENAMED
        if flag & pygit2.GIT_STATUS_INDEX_DELETED:
            return FileStatus.DELETED
        if flag & pygit2.GIT_STATUS_INDEX_TYPECHANGE:
            return FileStatus.TYPE_CHANGED
        if flag & pygit2.GIT_STATUS_INDEX_MODIFIED:
            return FileStatus.MODIFIED
        if flag & pygit2.GIT_STATUS_WT_NEW:
            return FileStatus.UNTRACKED
        if flag & pygit2.GIT_STATUS_WT_RENAMED:
            return FileStatus.RENAMED
        if flag & pygit2.GIT_STATUS_WT_DELETED:
            return FileStatus.DELETED
        if flag & pygit2.GIT_STATUS_WT_TYPECHANGE:
            return FileStatus.TYPE_CHANGED
        if flag & pygit2.GIT_STATUS_WT_MODIFIED:
            return FileStatus.MODIFIED
        # pygit2 reports GIT_STATUS_CURRENT (=0) for clean files; callers filter those out.
        return FileStatus.MODIFIED


__all__ = ["RepositoryManager", "unwrap"]
