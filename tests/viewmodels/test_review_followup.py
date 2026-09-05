import threading

import pygit2
import pytest
from src.core import operations as ops
from src.core.exceptions import DirtyWorkTreeError, GitError
from src.core.repository import RepositoryManager
from src.viewmodels.commands import (
    CommandProcessor,
    DropCommitCommand,
    EditCommitMessageCommand,
    GitCommand,
    SquashCommitsCommand,
)
from src.viewmodels.main_viewmodel import MainViewModel

SIG = pygit2.Signature("Review", "review@example.invalid", 1700000000, 0)


@pytest.fixture
def repo(tmp_path, qapp):
    r = pygit2.init_repository(str(tmp_path), initial_head="main")
    r.config["core.autocrlf"] = False
    r.config["user.name"] = "Review"
    r.config["user.email"] = "review@example.invalid"
    return RepositoryManager(str(tmp_path)), tmp_path


def commit(m, p, changes, message="commit"):
    for name, content in changes.items():
        f = p / name
        if content is None:
            f.unlink()
            m.repo.index.remove(name)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(content)
            m.repo.index.add(name)
    m.repo.index.write()
    return m.repo.create_commit(
        "HEAD",
        SIG,
        SIG,
        message,
        m.repo.index.write_tree(),
        [] if m.repo.head_is_unborn else [m.repo.head.target],
    )


def conflict(repo):
    m, p = repo
    commit(m, p, {"f.txt": b"base\n"})
    ops.create_branch(m, "feature")
    ops.checkout_branch(m, "feature")
    feature = commit(m, p, {"f.txt": b"feature\n"})
    ops.checkout_branch(m, "main")
    main = commit(m, p, {"f.txt": b"main\n"})
    vm = MainViewModel()
    vm.set_repository(m)
    return vm, main, feature


def test_fixed_reword_keeps_staged_and_unstaged(repo):
    m, p = repo
    commit(m, p, {"f.txt": b"base\n"})
    tip = commit(m, p, {"f.txt": b"tip\n"})
    (p / "f.txt").write_bytes(b"staged\n")
    m.repo.index.add("f.txt")
    m.repo.index.write()
    staged_oid = m.repo.index["f.txt"].id
    (p / "f.txt").write_bytes(b"unstaged\n")
    proc = CommandProcessor()
    proc.execute(EditCommitMessageCommand(m, str(tip), "new message"))
    assert proc.undo()
    assert m.repo.head.target == tip
    assert m.repo.index["f.txt"].id == staged_oid
    assert (p / "f.txt").read_bytes() == b"unstaged\n"


def test_fixed_drop_refuses_dirty_tracked(repo):
    m, p = repo
    commit(m, p, {"f.txt": b"base\n"})
    tip = commit(m, p, {"f.txt": b"tip\n"})
    (p / "f.txt").write_bytes(b"valuable\n")
    with pytest.raises(DirtyWorkTreeError):
        ops.drop_commit(m, str(tip))
    assert m.repo.head.target == tip
    assert (p / "f.txt").read_bytes() == b"valuable\n"


def test_fixed_squash_excludes_staged(repo):
    m, p = repo
    commit(m, p, {"f.txt": b"base\n"})
    mid = commit(m, p, {"f.txt": b"mid\n"})
    tip = commit(m, p, {"f.txt": b"tip\n"})
    (p / "other.txt").write_bytes(b"not in history\n")
    m.repo.index.add("other.txt")
    m.repo.index.write()
    ops.squash_commits(m, [str(tip), str(mid)], "squashed")
    assert "other.txt" not in m.repo[m.repo.head.target].tree
    assert m.repo.status()["other.txt"] & pygit2.GIT_STATUS_INDEX_NEW


def test_fixed_merge_target_stays_correct(repo):
    m, p = repo
    vm, main, feature = conflict(repo)
    ops.create_branch(m, "dev")
    ops.checkout_branch(m, "dev")
    dev = commit(m, p, {"d.txt": b"dev only\n"})
    vm.merge_branch("feature", target="main")
    vm.resolve_conflict("f.txt", "resolved\n")
    assert m.repo.lookup_branch("dev").target == dev
    assert m.repo[m.repo.lookup_branch("main").target].parent_ids == [main, feature]


def test_fixed_async_merge_context(repo):
    from PySide6.QtTest import QTest

    m, p = repo
    old_vm, main, feature = conflict(repo)
    vm = MainViewModel(async_enabled=True, merge_async_threshold=0)
    vm.set_repository(m)
    vm.merge_branch("feature")
    for _ in range(1000):
        if not vm.is_busy():
            break
        QTest.qWait(10)
    assert not vm.is_busy()
    state = vm.conflict_state()
    assert state["source_oid"] == str(feature)
    vm.resolve_conflict("f.txt", "resolved\n")
    assert not ops.is_merge_in_progress(m)
    assert m.repo[m.repo.head.target].parent_ids == [main, feature]


def test_fixed_clean_merge_cleans_state(repo):
    m, p = repo
    commit(m, p, {"f.txt": b"base\n"})
    ops.create_branch(m, "feature")
    ops.checkout_branch(m, "feature")
    commit(m, p, {"a.txt": b"feature\n"})
    ops.checkout_branch(m, "main")
    commit(m, p, {"b.txt": b"main\n"})
    ops.merge_branch(m, "feature")
    assert not ops.is_merge_in_progress(m)


def test_fixed_rebase_clears_vm_state(repo):
    m, p = repo
    vm, main, feature = conflict(repo)
    vm.rebase_branch("feature")
    assert vm.conflict_state()["conflicting_paths"] == ["f.txt"]
    vm.resolve_conflict("f.txt", "resolved\n")
    assert not ops.is_rebase_in_progress(m)
    assert vm.conflict_state() is None


def test_fixed_reword_abbrev_12(repo):
    m, p = repo
    commit(m, p, {"f.txt": b"base\n"})
    mid = commit(m, p, {"a.txt": b"mid\n"}, "old message")
    commit(m, p, {"b.txt": b"tip\n"})
    m.repo.config["core.abbrev"] = 12
    info = ops.edit_commit_message(m, str(mid), "new message")
    assert info.message.strip() == "new message"
    assert info.sha != str(mid)


def test_fixed_undo_failure_forwarded(repo):
    class FailedUndo(GitCommand):
        def execute(self):
            pass

        def undo(self):
            raise GitError("intentional failure")

        @property
        def name(self):
            return "failing undo"

    vm = MainViewModel()
    errors, logs = [], []
    vm.error_occurred.connect(errors.append)
    vm.log_message.connect(logs.append)
    vm.command_processor().execute(FailedUndo())
    vm.undo()
    assert errors == ["intentional failure"]
    assert not any("Undo succeeded" in line for line in logs)


def test_drop_must_preserve_untracked_file_colliding_with_parent(repo):
    m, p = repo
    commit(m, p, {"f.txt": b"old committed content\n"})
    tip = commit(m, p, {"f.txt": None}, "delete f")
    (p / "f.txt").write_bytes(b"valuable untracked content\n")
    try:
        ops.drop_commit(m, str(tip))
    except GitError:
        pass
    assert (p / "f.txt").read_bytes() == b"valuable untracked content\n"


def test_drop_undo_must_preserve_colliding_untracked_file(repo):
    m, p = repo
    commit(m, p, {"f.txt": b"base\n"})
    tip = commit(m, p, {"new.txt": b"committed content\n"})
    proc = CommandProcessor()
    proc.execute(DropCommitCommand(m, str(tip)))
    (p / "new.txt").write_bytes(b"valuable new content\n")
    proc.undo()
    assert (p / "new.txt").read_bytes() == b"valuable new content\n"


def test_reword_undo_must_not_move_different_branch_at_same_oid(repo):
    m, p = repo
    tip = commit(m, p, {"f.txt": b"base\n"})
    proc = CommandProcessor()
    proc.execute(EditCommitMessageCommand(m, str(tip), "new message"))
    new_oid = m.repo.head.target
    ops.create_branch(m, "other")
    ops.checkout_branch(m, "other")
    proc.undo()
    assert m.repo.lookup_branch("other").target == new_oid


def test_resolved_merge_single_undo_redo(repo):
    m, p = repo
    vm, main, feature = conflict(repo)
    vm.merge_branch("feature")
    vm.resolve_conflict("f.txt", "resolved\n")
    merged = m.repo.head.target
    vm.undo()
    assert m.repo.head.target == main
    errors = []
    vm.error_occurred.connect(errors.append)
    vm.redo()
    assert not errors, errors
    assert m.repo.head.target == merged


def test_undo_conflicted_merge_hides_conflict_panel_state(repo):
    m, p = repo
    vm, main, feature = conflict(repo)
    vm.merge_branch("feature")
    vm.undo()
    assert not ops.is_merge_in_progress(m)
    assert vm.conflict_state() is None


def test_continue_sees_real_external_git_add(repo):
    import subprocess

    m, p = repo
    vm, main, feature = conflict(repo)
    vm.merge_branch("feature")
    (p / "f.txt").write_bytes(b"external resolution\n")
    subprocess.run(["git", "add", "f.txt"], cwd=p, check=True, capture_output=True)
    vm.continue_operation()
    assert not ops.is_merge_in_progress(m)


def test_reword_before_merge_returns_reworded_commit(repo):
    m, p = repo
    commit(m, p, {"base.txt": b"base\n"}, "root")
    target = commit(m, p, {"target.txt": b"target\n"}, "old target")
    ops.create_branch(m, "feature")
    commit(m, p, {"main.txt": b"main\n"}, "main change")
    ops.checkout_branch(m, "feature")
    commit(m, p, {"feature.txt": b"feature\n"}, "feature change")
    ops.checkout_branch(m, "main")
    ops.merge_branch(m, "feature")
    commit(m, p, {"tip.txt": b"tip\n"}, "tip")
    old_tree = m.repo[m.repo.head.target].tree_id
    feature_oid = m.repo.lookup_branch("feature").target
    info = ops.edit_commit_message(m, str(target), "new target")
    assert info.message.strip() == "new target"
    assert m.repo[m.repo.head.target].tree_id == old_tree
    assert len(m.repo.revparse_single("HEAD~1").parent_ids) == 2
    assert m.repo.lookup_branch("feature").target == feature_oid
    rewritten_target = m.repo.revparse_single("HEAD~3")
    assert str(rewritten_target.id) == info.sha


@pytest.mark.parametrize("edit_root", [False, True])
def test_mid_reword_and_undo_preserve_local_changes(repo, edit_root):
    m, p = repo
    root = commit(m, p, {"f.txt": b"base\n"})
    middle = commit(m, p, {"f.txt": b"middle\n"})
    tip = commit(m, p, {"f.txt": b"tip\n"})
    (p / "f.txt").write_bytes(b"staged\n")
    m.repo.index.add("f.txt")
    m.repo.index.write()
    staged = m.repo.index["f.txt"].id
    (p / "f.txt").write_bytes(b"unstaged\n")
    proc = CommandProcessor()
    proc.execute(EditCommitMessageCommand(m, str(root if edit_root else middle), "new message"))
    assert m.repo.index["f.txt"].id == staged
    assert (p / "f.txt").read_bytes() == b"unstaged\n"
    assert proc.undo()
    assert m.repo.head.target == tip
    assert m.repo.index["f.txt"].id == staged
    assert (p / "f.txt").read_bytes() == b"unstaged\n"


def test_reword_merge_commit_preserves_resolved_tree(repo):
    m, p = repo
    vm, main, feature = conflict(repo)
    vm.merge_branch("feature")
    vm.resolve_conflict("f.txt", "manual resolution\n")
    merged = m.repo[m.repo.head.target]
    commit(m, p, {"tip.txt": b"tip\n"})
    info = ops.edit_commit_message(m, str(merged.id), "renamed merge")
    rewritten = m.repo[pygit2.Oid(hex=info.sha)]
    assert rewritten.tree_id == merged.tree_id
    assert rewritten.parent_ids == [main, feature]
    assert rewritten.message == "renamed merge"
    assert m.repo[m.repo.head.target].parent_ids == [rewritten.id]


def test_drop_rebase_conflict_lists_real_paths(repo):
    m, p = repo
    commit(m, p, {"f.txt": b"A\n"}, "base")
    middle = commit(m, p, {"f.txt": b"B\n"}, "middle")
    commit(m, p, {"f.txt": b"C\n"}, "tip")
    vm = MainViewModel()
    vm.set_repository(m)
    vm.drop_commit(str(middle))
    assert ops.is_rebase_in_progress(m)
    assert ops.conflicting_paths(m) == ["f.txt"]
    assert vm.conflict_state()["conflicting_paths"] == ["f.txt"]


@pytest.mark.parametrize("kind", ["ignored", "file_to_dir", "dir_to_file"])
def test_drop_protects_ignored_and_directory_collisions(repo, kind):
    m, p = repo
    old_path = "item/child.txt" if kind == "dir_to_file" else "item"
    commit(m, p, {old_path: b"old\n", ".gitignore": b"item\n" if kind == "ignored" else b""})
    tip = commit(m, p, {old_path: None}, "delete")
    if kind == "dir_to_file":
        (p / "item").rmdir()
    new_path = "item/valuable.txt" if kind == "file_to_dir" else "item"
    local = p / new_path
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"valuable\n")
    with pytest.raises(DirtyWorkTreeError):
        ops.drop_commit(m, str(tip))
    assert m.repo.head.target == tip
    assert local.read_bytes() == b"valuable\n"


def test_drop_and_undo_allow_unrelated_untracked_files(repo):
    m, p = repo
    base = commit(m, p, {"f.txt": b"base\n"})
    tip = commit(m, p, {"f.txt": b"tip\n"})
    (p / "notes.txt").write_bytes(b"keep\n")
    proc = CommandProcessor()
    proc.execute(DropCommitCommand(m, str(tip)))
    assert m.repo.head.target == base
    assert proc.undo()
    assert m.repo.head.target == tip
    assert (p / "notes.txt").read_bytes() == b"keep\n"


@pytest.mark.parametrize("kind", ["drop", "squash"])
def test_undo_checks_branch_identity_for_other_rewrites(repo, kind):
    m, p = repo
    commit(m, p, {"f.txt": b"base\n"})
    middle = commit(m, p, {"f.txt": b"middle\n"})
    tip = commit(m, p, {"f.txt": b"tip\n"})
    command = (
        DropCommitCommand(m, str(tip))
        if kind == "drop"
        else SquashCommitsCommand(m, [str(tip), str(middle)], "squashed")
    )
    proc = CommandProcessor()
    proc.execute(command)
    new_head = m.repo.head.target
    ops.create_branch(m, "other")
    ops.checkout_branch(m, "other")
    assert not proc.undo()
    assert proc.peek_undo_command() is command
    assert m.repo.lookup_branch("other").target == new_head


@pytest.mark.parametrize("kind", ["drop", "reword", "squash"])
def test_async_history_rewrite_uses_own_repo_and_gui_history(repo, qtbot, monkeypatch, kind):
    from PySide6.QtCore import QThread, QTimer
    from src.viewmodels import commands

    m, p = repo
    commit(m, p, {"root.txt": b"root\n"})
    first = commit(m, p, {"a.txt": b"a\n"})
    second = commit(m, p, {"b.txt": b"b\n"})
    tip = commit(m, p, {"c.txt": b"c\n"})
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(m)
    errors, stack_threads, work_threads = [], [], []
    vm.error_occurred.connect(errors.append)
    vm.command_processor().stack_changed.connect(
        lambda: stack_threads.append(QThread.currentThread())
    )
    operation_name = {
        "drop": "drop_commit",
        "reword": "edit_commit_message",
        "squash": "squash_commits",
    }[kind]
    original = getattr(commands, operation_name)
    started, release = threading.Event(), threading.Event()

    def blocked(manager, *args, **kwargs):
        assert manager is not m
        assert manager.repo is not m.repo
        work_threads.append(QThread.currentThread())
        started.set()
        assert release.wait(5)
        return original(manager, *args, **kwargs)

    monkeypatch.setattr(commands, operation_name, blocked)
    args = {
        "drop": (str(first),),
        "reword": (str(first), "new message"),
        "squash": ([str(second), str(first)], "squashed"),
    }[kind]
    ticks = []
    getattr(vm, operation_name)(*args)
    try:
        qtbot.waitUntil(started.is_set)
        QTimer.singleShot(0, lambda: ticks.append(True))
        qtbot.waitUntil(lambda: bool(ticks))
        assert vm.is_busy()
        assert not vm.command_processor().can_undo
    finally:
        release.set()
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=15000)
    assert not errors
    assert m.repo.head.target != tip
    vm.undo()
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=15000)
    assert m.repo.head.target == tip
    vm.redo()
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=15000)
    assert not errors
    assert len(work_threads) == 2
    assert all(t != vm.thread() for t in work_threads)
    assert stack_threads and all(t == vm.thread() for t in stack_threads)


@pytest.mark.parametrize("force", [False, True])
def test_async_remote_checkout_targets_correct_branch(repo, qtbot, monkeypatch, force):
    from src.viewmodels import commands

    m, p = repo
    root = commit(m, p, {"f.txt": b"base\n"})
    remote = pygit2.init_repository(str(p / "origin.git"), bare=True)
    m.repo.remotes.create("origin", remote.path)
    ops.create_branch(m, "feature")
    ops.checkout_branch(m, "feature")
    remote_tip = commit(m, p, {"remote.txt": b"remote\n"})
    ops.push(m, "origin", "refs/heads/feature")
    ops.reset(m, str(root), mode="hard")
    if force:
        commit(m, p, {"local.txt": b"local only\n"})
    ops.checkout_branch(m, "main")
    main_tip = commit(m, p, {"main.txt": b"keep main\n"})
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(m)
    original = commands.fetch
    started, release = threading.Event(), threading.Event()
    errors, callbacks = [], []
    vm.error_occurred.connect(errors.append)

    def blocked(manager, *args, **kwargs):
        assert manager is not m
        started.set()
        assert release.wait(5)
        return original(manager, *args, **kwargs)

    monkeypatch.setattr(commands, "fetch", blocked)
    if force:
        vm.reset_local_branch_to_remote("origin/feature")
    else:
        vm.fetch_and_checkout_remote_branch(
            "origin/feature", on_success=lambda: callbacks.append(True)
        )
    try:
        qtbot.waitUntil(started.is_set)
        assert vm.is_busy()
        assert not callbacks
    finally:
        release.set()
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=10000)
    assert not errors
    assert m.repo.head.shorthand == "feature"
    assert m.repo.head.target == remote_tip
    assert m.repo.lookup_branch("main").target == main_tip
    assert callbacks == ([] if force else [True])
    vm.undo()
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=10000)
    assert not errors
    assert m.repo.head.shorthand == "main"
    assert m.repo.head.target == main_tip


def test_failed_async_fetch_does_not_checkout_or_run_continuation(repo, qtbot, monkeypatch):
    from src.viewmodels import commands

    m, p = repo
    tip = commit(m, p, {"f.txt": b"base\n"})
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(m)
    errors, callbacks = [], []
    vm.error_occurred.connect(errors.append)

    def failed(*args, **kwargs):
        raise GitError("fetch failed")

    monkeypatch.setattr(commands, "fetch", failed)
    vm.fetch_and_checkout_remote_branch("origin/feature", on_success=lambda: callbacks.append(True))
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert errors == ["fetch failed"]
    assert not callbacks
    assert m.repo.head.target == tip
    assert m.repo.head.shorthand == "main"
    assert not vm.command_processor().can_undo


def test_async_rebase_continue_and_history_roundtrip(repo, qtbot, monkeypatch):
    from PySide6.QtCore import QThread

    m, p = repo
    old_vm, original_head, feature = conflict(repo)
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(m)
    vm.rebase_branch("feature")
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=10000)
    assert vm.conflict_state()["conflicting_paths"] == ["f.txt"]
    original = ops.complete_rebase_continue
    threads = []

    def checked(manager):
        assert manager is not m
        threads.append(QThread.currentThread())
        return original(manager)

    monkeypatch.setattr(ops, "complete_rebase_continue", checked)
    vm.resolve_conflict("f.txt", "resolved\n")
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=10000)
    assert vm.conflict_state() is None
    assert not ops.is_rebase_in_progress(m)
    completed = m.repo.head.target
    assert m.repo[completed].parent_ids == [feature]
    vm.undo()
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=10000)
    assert m.repo.head.target == original_head
    vm.redo()
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=10000)
    assert m.repo.head.target == completed
    assert threads and all(t != vm.thread() for t in threads)
