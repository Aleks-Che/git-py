"""Persistent conflict-state panel.

Shown above the graph while a merge / rebase / cherry-pick / revert is
stopped on conflicts. The panel lists the conflicting files, opens the
per-file resolution dialog (``Resolve…``), and offers the operation
level actions: **Continue** (finish the merge / rebase once every
conflict is resolved — including files resolved with an external
editor) and **Abort** (roll the operation back).

The panel is passive per ``docs/DEVELOPMENT_RULES.md``: it renders
:attr:`MainViewModel.conflict_state_changed` snapshots and emits
intent signals; ``MainWindow`` routes those to the ViewModel.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_OPERATION_LABELS = {
    "merge": "Merge",
    "rebase": "Rebase",
    "cherry-pick": "Cherry-pick",
    "revert": "Revert",
}


class ConflictPanel(QFrame):
    """Banner with the conflicting file list and Continue/Abort actions."""

    resolve_requested = Signal(str)
    continue_requested = Signal()
    # Carries the operation name (``"merge"`` / ``"rebase"`` / ...) so
    # the router can pick the right abort verb without re-reading VM
    # state.
    abort_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("conflictPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._operation: str | None = None

        layout = QVBoxLayout(self)

        self._title = QLabel("Conflict")
        self._title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._title)

        self._subtitle = QLabel("")
        layout.addWidget(self._subtitle)

        self._files = QListWidget(self)
        self._files.setMaximumHeight(100)
        self._files.itemDoubleClicked.connect(self._on_item_activated)
        layout.addWidget(self._files)

        button_row = QHBoxLayout()
        self._resolve_btn = QPushButton("Resolve…", self)
        self._resolve_btn.clicked.connect(self._on_resolve_clicked)
        button_row.addWidget(self._resolve_btn)

        self._continue_btn = QPushButton("Continue", self)
        self._continue_btn.clicked.connect(self.continue_requested.emit)
        button_row.addWidget(self._continue_btn)

        self._abort_btn = QPushButton("Abort", self)
        self._abort_btn.clicked.connect(self._on_abort_clicked)
        button_row.addWidget(self._abort_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.hide()

    # ----- public API ---------------------------------------------------

    def set_state(self, state: dict | None) -> None:
        """Render a ``conflict_state_changed`` snapshot (or hide)."""
        if not state or not state.get("in_progress"):
            self._operation = None
            self.hide()
            return
        self._operation = state.get("operation")
        paths = list(state.get("conflicting_paths") or [])
        op_label = _OPERATION_LABELS.get(self._operation, self._operation or "Operation")
        self._title.setText(
            f"{op_label} in progress — {len(paths)} conflicting file(s)",
        )
        context_bits: list[str] = []
        source = state.get("source")
        target = state.get("target")
        upstream = state.get("upstream")
        if source:
            context_bits.append(f"merging {source!r}" + (f" into {target!r}" if target else ""))
        elif upstream:
            context_bits.append(f"onto {upstream!r}")
        self._subtitle.setText("; ".join(context_bits))
        self._subtitle.setVisible(bool(context_bits))

        self._files.clear()
        for path in paths:
            QListWidgetItem(path, self._files)
        has_paths = bool(paths)
        self._resolve_btn.setEnabled(has_paths)
        # Continue is meaningful for the operations the VM can finish
        # (merge / rebase); cherry-pick / revert finish via the normal
        # commit panel instead.
        self._continue_btn.setEnabled(self._operation in ("merge", "rebase"))
        self._abort_btn.setEnabled(self._operation in ("merge", "rebase"))
        self.show()

    def operation(self) -> str | None:
        """Return the operation name of the currently shown state."""
        return self._operation

    # ----- internals ----------------------------------------------------

    def _selected_path(self) -> str | None:
        item = self._files.currentItem()
        if item is None and self._files.count() == 1:
            item = self._files.item(0)
        return item.text() if item is not None else None

    def _on_resolve_clicked(self) -> None:
        path = self._selected_path()
        if path:
            self.resolve_requested.emit(path)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        self.resolve_requested.emit(item.text())

    def _on_abort_clicked(self) -> None:
        if self._operation:
            self.abort_requested.emit(self._operation)


__all__ = ["ConflictPanel"]
