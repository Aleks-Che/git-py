"""Right panel: container that swaps the WIP and commit-detail views.

The right side of the main window has three distinct modes:

* **Commit-input mode** — :class:`CommitPanel` is shown. This is the
  state when the user has selected the WIP (uncommitted-changes) node
  in the graph. Staging lists at the top, commit input fields at the
  bottom.
* **Commit-detail mode** — :class:`CommitDetailPanel` is shown. This
  is the state when the user has selected a real commit. Read-only
  message / info / file list.
* **Other-worktree mode** — branch, path, changed-file count and a button
  requesting a repository tab for that checkout.

The panel is hidden entirely when no commit is selected. The
:class:`MainViewModel.selection_changed` signal is the single source
of truth for which mode (if any) is active: ``None`` → hidden,
``WIP_SHA`` → commit-input, a sibling WIP ID → worktree navigation,
and a real SHA → commit-detail.

A *click-same-commit-toggles-off* policy is implemented at the VM
level (see :meth:`MainViewModel.select_commit`); the panel just
reacts to the resulting ``selection_changed`` emissions.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from src.viewmodels.graph_viewmodel import WIP_SHA
from src.viewmodels.main_viewmodel import MainViewModel

from .commit_detail_panel import CommitDetailPanel
from .commit_panel import CommitPanel


class RightPanel(QWidget):
    """Top-level container for the right side of the main window.

    The widget is a thin shell: it owns the sub-panels and shows
    exactly one of them (or nothing) at a time. It is driven by
    :attr:`MainViewModel.selection_changed` — a single signal connects
    the central VM to the panel's visible state.
    """

    def __init__(self, main_view_model: MainViewModel, parent=None) -> None:
        super().__init__(parent)
        self._main_vm = main_view_model

        self._commit_input = CommitPanel(main_view_model, self)
        self._commit_detail = CommitDetailPanel(main_view_model, self)
        self._worktree_panel = QWidget(self)
        worktree_layout = QVBoxLayout(self._worktree_panel)
        title = QLabel("Uncommitted changes in another worktree", self._worktree_panel)
        title.setWordWrap(True)
        worktree_layout.addWidget(title)
        self._worktree_info = QLabel(self._worktree_panel)
        self._worktree_info.setTextFormat(Qt.TextFormat.PlainText)
        self._worktree_info.setWordWrap(True)
        self._worktree_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        worktree_layout.addWidget(self._worktree_info)
        self._open_worktree_button = QPushButton("Open worktree in new tab", self._worktree_panel)
        self._open_worktree_button.clicked.connect(lambda: self._main_vm.open_worktree())
        self._main_vm.busy_changed.connect(
            lambda busy: self._open_worktree_button.setEnabled(not busy),
        )
        self._open_worktree_button.setEnabled(not self._main_vm.is_busy())
        worktree_layout.addWidget(self._open_worktree_button)
        worktree_layout.addStretch()

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._commit_input)   # index 0
        self._stack.addWidget(self._commit_detail)  # index 1
        self._stack.addWidget(self._worktree_panel)  # index 2
        self._stack.setCurrentIndex(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._stack)

        # The panel starts hidden; ``_on_selection_changed`` is
        # connected below so the first emission (or the initial
        # ``None`` state) controls visibility.
        self.setVisible(False)

        self._main_vm.selection_changed.connect(self._on_selection_changed)
        # Synchronise with the VM's current state in case the panel
        # was constructed after a selection was already made.
        self._on_selection_changed(self._main_vm.selected_commit_sha())

    # ----- selection-driven mode switching ----------------------------

    def _on_selection_changed(self, sha: str | None) -> None:
        """Show / hide the panel and pick the right sub-view.

        ``None`` → hidden, ``WIP_SHA`` → commit-input, sibling WIP →
        worktree navigation, real SHA → commit-detail.

        When leaving the WIP / commit-input view the file selection
        in the commit panel VM is cleared so the diff view (which
        replaces the graph) is hidden. The commit-detail panel
        clears its own selection inside :meth:`show_commit`; we
        also clear it explicitly when switching back to WIP or
        hiding the panel, since ``show_commit`` is not on that
        path.
        """
        if sha is None:
            self._main_vm.commit_panel_view_model().select_file(None)
            self._commit_detail.select_file(None)
            self.setVisible(False)
            return
        self.setVisible(True)
        worktree = self._main_vm.graph_view_model().worktree_changes(sha)
        if worktree is not None:
            self._main_vm.commit_panel_view_model().select_file(None)
            self._commit_detail.select_file(None)
            branch = worktree.branch or "Detached HEAD"
            self._worktree_info.setText(
                f"Branch: {branch}\n\n{worktree.path}\n\n"
                f"{worktree.count} changed file(s)",
            )
            self._stack.setCurrentIndex(2)
        elif sha == WIP_SHA:
            self._commit_detail.select_file(None)
            self._stack.setCurrentIndex(0)
            self._commit_input._refresh_all()
        else:
            self._main_vm.commit_panel_view_model().select_file(None)
            self._stack.setCurrentIndex(1)
            self._commit_detail.show_commit(sha)


__all__ = ["RightPanel"]
