"""Right panel: container that swaps the WIP and commit-detail views.

The right side of the main window has two distinct modes:

* **Commit-input mode** — :class:`CommitPanel` is shown. This is the
  state when the user has selected the WIP (uncommitted-changes) node
  in the graph. Staging lists at the top, commit input fields at the
  bottom.
* **Commit-detail mode** — :class:`CommitDetailPanel` is shown. This
  is the state when the user has selected a real commit. Read-only
  message / info / file list.

The panel is hidden entirely when no commit is selected. The
:class:`MainViewModel.selection_changed` signal is the single source
of truth for which mode (if any) is active: ``None`` → hidden,
``WIP_SHA`` → commit-input, anything else → commit-detail for that
SHA.

A *click-same-commit-toggles-off* policy is implemented at the VM
level (see :meth:`MainViewModel.select_commit`); the panel just
reacts to the resulting ``selection_changed`` emissions.
"""
from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from src.viewmodels.graph_viewmodel import WIP_SHA
from src.viewmodels.main_viewmodel import MainViewModel

from .commit_detail_panel import CommitDetailPanel
from .commit_panel import CommitPanel


class RightPanel(QWidget):
    """Top-level container for the right side of the main window.

    The widget is a thin shell: it owns the two sub-panels and shows
    exactly one of them (or nothing) at a time. It is driven by
    :attr:`MainViewModel.selection_changed` — a single signal connects
    the central VM to the panel's visible state.
    """

    def __init__(self, main_view_model: MainViewModel, parent=None) -> None:
        super().__init__(parent)
        self._main_vm = main_view_model

        self._commit_input = CommitPanel(main_view_model, self)
        self._commit_detail = CommitDetailPanel(main_view_model, self)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._commit_input)   # index 0
        self._stack.addWidget(self._commit_detail)  # index 1
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

        ``None`` → hidden. ``WIP_SHA`` → commit-input. Any other
        value → commit-detail populated for that SHA.

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
        if sha == WIP_SHA:
            self._commit_detail.select_file(None)
            self._stack.setCurrentIndex(0)
            self._commit_input._refresh_all()
        else:
            self._main_vm.commit_panel_view_model().select_file(None)
            self._stack.setCurrentIndex(1)
            self._commit_detail.show_commit(sha)


__all__ = ["RightPanel"]
