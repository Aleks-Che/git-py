"""Per-file patch extraction must not read unrelated blobs."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pygit2
import pytest
from src.core.exceptions import GitError
from src.core.file_diff import extract_file_patch, workdir_file_diff
from src.core.repository import RepositoryManager


def test_only_selected_delta_materializes_patch():
    class Diff:
        deltas = [
            SimpleNamespace(old_file=SimpleNamespace(path=old), new_file=SimpleNamespace(path=new))
            for old, new in [('huge.bin', 'huge.bin'), ('before.txt', 'after.txt')]
        ]

        def __iter__(self):
            pytest.fail('Iterating patches loads unrelated file contents')

        def __getitem__(self, index):
            assert index == 1
            return SimpleNamespace(text='selected patch')

    assert extract_file_patch(Diff(), 'before.txt') == 'selected patch'
    assert extract_file_patch(Diff(), 'after.txt') == 'selected patch'
    assert extract_file_patch(Diff(), 'absent') == ''


def test_lazy_patch_errors_become_domain_errors():
    class Diff:
        deltas = [SimpleNamespace(
            old_file=SimpleNamespace(path='file'), new_file=SimpleNamespace(path='file'),
        )]

        def __getitem__(self, _index):
            raise pygit2.GitError('cannot read blob')

    with pytest.raises(GitError, match='cannot read blob'):
        extract_file_patch(Diff(), 'file')


def test_newborn_staged_diff_reads_index_version(tmp_git_repo):
    manager = RepositoryManager(str(tmp_git_repo))
    path = tmp_git_repo / 'new.txt'
    path.write_text('staged\n')
    manager.repo.index.add('new.txt')
    manager.repo.index.write()
    path.write_text('unstaged\n')
    text = workdir_file_diff(manager, 'new.txt', staged=True)
    assert '+staged' in text
    assert '+unstaged' not in text


def test_untracked_binary_is_not_decoded_as_text(tmp_git_repo, monkeypatch):
    manager = RepositoryManager(str(tmp_git_repo))
    (tmp_git_repo / 'new.bin').write_bytes(b'\0' + b'x' * 100_000)

    def unexpected(*_args, **_kwargs):
        pytest.fail('Binary detection must only read a small prefix')

    monkeypatch.setattr(Path, 'read_bytes', unexpected)
    monkeypatch.setattr(Path, 'read_text', unexpected)
    assert 'Binary file' in workdir_file_diff(manager, 'new.bin')
