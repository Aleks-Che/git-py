"""Shared branch payload for the references tree and graph."""
from PySide6.QtCore import QMimeData

BRANCH_MIME = "application/x-git-py-branch-chip"
BRANCH_KIND_MIME = "application/x-git-py-branch-kind"
LOCAL_BRANCH = "local_branch"
REMOTE_BRANCH = "remote_branch"


def branch_mime_data(name: str, kind: str) -> QMimeData:
    data = QMimeData()
    data.setText(name)
    data.setData(BRANCH_MIME, name.encode("utf-8"))
    data.setData(BRANCH_KIND_MIME, kind.encode("utf-8"))
    return data


def read_branch_mime(data: QMimeData) -> tuple[str, str] | None:
    if not data.hasText() or not data.text():
        return None
    try:
        if data.hasFormat(BRANCH_KIND_MIME):
            kind = bytes(data.data(BRANCH_KIND_MIME)).decode("utf-8")
            if kind in (LOCAL_BRANCH, REMOTE_BRANCH):
                return data.text(), kind
        elif data.hasFormat(BRANCH_MIME):
            return data.text(), LOCAL_BRANCH
    except UnicodeDecodeError:
        pass
    return None
