"""Left references panel: local/remote branches, tags, stash.

The widget is a passive view bound to
:class:`src.viewmodels.branch_panel_viewmodel.BranchPanelViewModel`
for data and to :class:`src.viewmodels.main_viewmodel.MainViewModel`
for the mutating verb methods. It owns zero Git state and never
talks to ``pygit2`` directly.

Layout: a single :class:`QTreeWidget` with three top-level groups:

* **Branches** (expandable, expanded by default)
    * **Local** — local branches; the current ``HEAD`` is rendered
      in bold. Double-clicking switches to that branch.
    * **Remote** — remote-tracking branches.
* **Tags** — all tags.
* **Stash** — stash entries (``stash@{0}`` … ``stash@{N}``).

Context menu (right-click):

* On a local branch: **Checkout**, **Create Branch from here…**,
  **Rename…**, **Delete**.
* On a remote branch: **Create Branch from here…** (Stage 5 will
  add a real "create tracking branch and checkout" option; for now
  we just refuse remote-checkout with a clear message).
* On a tag: **Create Branch from here…**.
* On empty space (or top-level group): **Create Branch from HEAD…**.

Drag-and-drop is enabled on local-branch leaves and on the empty
top-level area, but a real merge / rebase is a Stage 5 feature —
for now the drop event shows an informational :class:`QMessageBox`.

All actions ultimately call the corresponding verb method on
:class:`MainViewModel`; the panel never raises Git errors itself,
the ViewModel forwards them to its ``error_occurred`` signal.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
)

from src.core.exceptions import GitError
from src.viewmodels.branch_panel_viewmodel import BranchPanelViewModel
from src.viewmodels.main_viewmodel import MainViewModel

# Item data roles. ``UserRole`` is the canonical Qt role for app data;
# we pick ``UserRole + 1`` for the kind discriminator so ``UserRole``
# itself can hold the name.
_ROLE_KIND = Qt.ItemDataRole.UserRole + 1
_ROLE_NAME = Qt.ItemDataRole.UserRole

# Discriminator values for ``_ROLE_KIND``.
_KIND_LOCAL_BRANCH = "local_branch"
_KIND_REMOTE_BRANCH = "remote_branch"
_KIND_TAG = "tag"
_KIND_STASH = "stash"

# Group header labels.
_GROUP_BRANCHES = "Branches"
_GROUP_LOCAL = "Local"
_GROUP_REMOTE = "Remote"
_GROUP_TAGS = "Tags"
_GROUP_STASH = "Stash"

_PLACEHOLDER_TEXT = "No repository opened"


class LeftPanel(QTreeWidget):
    """References tree (branches, tags, stash) bound to the two ViewModels."""

    def __init__(
        self,
        view_model: BranchPanelViewModel,
        main_view_model: MainViewModel,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._vm = view_model
        self._main_vm = main_view_model

        self.setHeaderLabel("References")
        self.setUniformRowHeights(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.setExpandsOnDoubleClick(False)  # we handle double-click ourselves
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

        self._placeholder = QTreeWidgetItem([_PLACEHOLDER_TEXT])
        self._placeholder.setDisabled(True)
        self.addTopLevelItem(self._placeholder)

        self._group_branches: QTreeWidgetItem | None = None
        self._group_local: QTreeWidgetItem | None = None
        self._group_remote: QTreeWidgetItem | None = None
        self._group_tags: QTreeWidgetItem | None = None
        self._group_stash: QTreeWidgetItem | None = None

        self._vm.references_changed.connect(self._rebuild)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.customContextMenuRequested.connect(self._on_context_menu)

    # ----- build / rebuild --------------------------------------------

    def _rebuild(self) -> None:
        """Rebuild the whole tree from the current VM snapshot."""
        self.clear()
        if self._vm.repository_manager() is None:
            placeholder = QTreeWidgetItem([_PLACEHOLDER_TEXT])
            placeholder.setDisabled(True)
            self.addTopLevelItem(placeholder)
            return

        self._group_branches = QTreeWidgetItem([_GROUP_BRANCHES])
        self._group_local = QTreeWidgetItem([_GROUP_LOCAL])
        self._group_remote = QTreeWidgetItem([_GROUP_REMOTE])
        self._group_branches.addChild(self._group_local)
        self._group_branches.addChild(self._group_remote)
        self.addTopLevelItem(self._group_branches)
        self._group_branches.setExpanded(True)
        self._group_local.setExpanded(True)

        current = self._vm.current_branch_name()
        bold = QFont()
        bold.setBold(True)
        for branch in self._vm.local_branches():
            item = QTreeWidgetItem([branch.name])
            item.setData(0, _ROLE_KIND, _KIND_LOCAL_BRANCH)
            item.setData(0, _ROLE_NAME, branch.name)
            if branch.name == current:
                item.setFont(0, bold)
                item.setText(0, f"{branch.name}  (HEAD)")
            self._group_local.addChild(item)
        for branch in self._vm.remote_branches():
            item = QTreeWidgetItem([branch.name])
            item.setData(0, _ROLE_KIND, _KIND_REMOTE_BRANCH)
            item.setData(0, _ROLE_NAME, branch.name)
            self._group_remote.addChild(item)

        self._group_tags = QTreeWidgetItem([_GROUP_TAGS])
        self.addTopLevelItem(self._group_tags)
        self._group_tags.setExpanded(False)
        for tag in self._vm.tags():
            item = QTreeWidgetItem([tag.name])
            item.setData(0, _ROLE_KIND, _KIND_TAG)
            item.setData(0, _ROLE_NAME, tag.name)
            self._group_tags.addChild(item)
        if not self._group_tags.childCount():
            self._group_tags.setDisabled(True)

        self._group_stash = QTreeWidgetItem([_GROUP_STASH])
        self.addTopLevelItem(self._group_stash)
        self._group_stash.setExpanded(False)
        for entry in self._vm.stash_list():
            label = f"stash@{{{entry.index}}}: {entry.message}"
            item = QTreeWidgetItem([label])
            item.setData(0, _ROLE_KIND, _KIND_STASH)
            item.setData(0, _ROLE_NAME, str(entry.index))
            self._group_stash.addChild(item)
        if not self._group_stash.childCount():
            self._group_stash.setDisabled(True)

        self._update_drag_state()

    def _update_drag_state(self) -> None:
        """Enable drag on local-branch leaves; the drop handler is a Stage 5 stub."""
        # QTreeWidget is drag-enabled wholesale when any item has
        # ItemIsDragEnabled. We toggle that flag on individual items in
        # the local-branch group.
        if self._group_local is None:
            return
        for i in range(self._group_local.childCount()):
            leaf = self._group_local.child(i)
            leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsDragEnabled)

    # ----- user actions ------------------------------------------------

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Double-click on a local branch → checkout; remote / tag → create branch."""
        kind = item.data(0, _ROLE_KIND)
        name = item.data(0, _ROLE_NAME)
        if kind == _KIND_LOCAL_BRANCH and name:
            self._main_vm.checkout_branch(name)
        elif kind == _KIND_REMOTE_BRANCH and name:
            self._main_vm.create_branch(name)
        elif kind == _KIND_TAG and name:
            self._main_vm.create_branch(name)

    def _on_context_menu(self, position) -> None:  # noqa: ANN001 - QPoint
        item = self.itemAt(position)
        if item is None:
            return
        actions = self._context_menu_actions(item)
        if not actions:
            return
        menu = QMenu(self)
        for action in actions:
            menu.addAction(action)
        menu.exec(self.viewport().mapToGlobal(position))

    def _context_menu_actions(self, item: QTreeWidgetItem) -> list[QAction]:
        """Return the list of actions a context menu would show for ``item``.

        Exposed for tests: building a real :class:`QMenu` and
        ``.exec()``ing it would require a running event loop and a
        mouse position, so we split the action-building logic out so
        the menu can be inspected synchronously.
        """
        kind = item.data(0, _ROLE_KIND)
        name = item.data(0, _ROLE_NAME)
        if not kind or not name:
            return []
        actions: list[QAction] = []
        if kind == _KIND_LOCAL_BRANCH:
            checkout = QAction("Checkout", self)
            checkout.triggered.connect(lambda: self._main_vm.checkout_branch(name))
            actions.append(checkout)
            actions.append(QAction(self))  # visual separator; exec skips it
            actions[-1].setSeparator(True)
            create_from = QAction(f"Create Branch from {name}…", self)
            create_from.triggered.connect(lambda: self._prompt_create_branch(from_name=name))
            actions.append(create_from)
            rename = QAction("Rename…", self)
            rename.triggered.connect(lambda: self._prompt_rename(old_name=name))
            actions.append(rename)
            delete = QAction("Delete…", self)
            delete.triggered.connect(lambda: self._prompt_delete(name))
            actions.append(delete)
        elif kind == _KIND_REMOTE_BRANCH:
            create_from = QAction(f"Create Branch from {name}…", self)
            create_from.triggered.connect(lambda: self._prompt_create_branch(from_name=name))
            actions.append(create_from)
        elif kind == _KIND_TAG:
            create_from = QAction(f"Create Branch from {name}…", self)
            create_from.triggered.connect(lambda: self._prompt_create_branch(from_name=name))
            actions.append(create_from)
        elif kind == _KIND_STASH:
            apply = QAction("Apply (Stage 7)", self)
            apply.setEnabled(False)
            actions.append(apply)
        return actions

    # ----- drag-and-drop stub -----------------------------------------

    def dropEvent(self, event) -> None:  # noqa: ANN001, N802 - QDropEvent + Qt override
        if event.mimeData().hasText():
            QMessageBox.information(
                self,
                "Drag and drop",
                "Drag-and-drop merge/rebase will be implemented on Stage 5.",
            )
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ----- prompts (small dialogs) ------------------------------------

    def _prompt_create_branch(self, from_name: str | None = None) -> None:
        """Ask for a branch name; create it at ``from_name`` (HEAD if ``None``)."""
        name, ok = QInputDialog.getText(
            self,
            "Create Branch",
            f"New branch name (from {'HEAD' if from_name is None else from_name!r}):",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        self._main_vm.create_branch(name, target_sha=from_name)

    def _prompt_rename(self, old_name: str) -> None:
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Branch",
            f"Rename {old_name!r} to:",
            QLineEdit.EchoMode.Normal,
            old_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        try:
            self._main_vm.rename_branch(old_name, new_name)
        except GitError as exc:
            QMessageBox.warning(self, "Rename Branch", str(exc))

    def _prompt_delete(self, name: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete Branch",
            f"Delete branch {name!r}? This cannot be easily undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._main_vm.delete_branch(name)
        except GitError as exc:
            QMessageBox.warning(self, "Delete Branch", str(exc))


__all__ = ["LeftPanel"]
