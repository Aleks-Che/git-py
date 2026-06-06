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

Every group row carries an explicit chevron icon
(``QStyle.SP_ArrowRight`` when collapsed, ``QStyle.SP_ArrowDown``
when expanded) so the expand/collapse state is visible at a glance
even when the platform's default branch indicator is hard to see
on a dark surface. The chevron is updated by the
``itemExpanded`` / ``itemCollapsed`` signals; leaf rows (branches,
tags, stash entries) keep no icon.

Context menu (right-click):

* On a local branch: **Checkout**, **Merge into current…**,
  **Rebase onto current…**, **Create Branch from here…**,
  **Rename…**, **Delete**, **Cherry-pick HEAD…** (a placeholder —
  real cherry-pick is in Stage 5+).
* On a remote branch: **Create Branch from here…**.
* On a tag: **Create Branch from here…**, **Cherry-pick {tag}…**.
* On empty space (or top-level group): **Create Branch from HEAD…**.

Drag-and-drop: dragging a local branch onto another local branch
opens a small menu with **Merge {source} into {target}** and
**Rebase {source} onto {target}**. The actual operations are
invoked on :class:`MainViewModel`; the panel never touches Git
state directly. The ``source == target`` and ``source is the
current branch`` cases are filtered out — both would be no-ops
or merge into self.

All actions ultimately call the corresponding verb method on
:class:`MainViewModel`; the panel never raises Git errors itself,
the ViewModel forwards them to its ``error_occurred`` signal.
"""
from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import (
    QAction,
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QIcon,
    QImage,
    QPixmap,
)
from PySide6.QtWidgets import (
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
)

from src.core.exceptions import GitError
from src.utils.theme import DARK_THEME
from src.viewmodels.branch_panel_viewmodel import BranchPanelViewModel
from src.viewmodels.main_viewmodel import MainViewModel

# Item data roles. ``UserRole`` is the canonical Qt role for app data;
# we pick ``UserRole + 1`` for the kind discriminator so ``UserRole``
# itself can hold the name. ``UserRole + 2`` marks a group row
# (Branches / Local / Remote / Tags / Stash) so the chevron icon
# logic can tell a group from a leaf and from the placeholder even
# when the group happens to be empty.
_ROLE_KIND = Qt.ItemDataRole.UserRole + 1
_ROLE_NAME = Qt.ItemDataRole.UserRole
_ROLE_IS_GROUP = Qt.ItemDataRole.UserRole + 2

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
        self.itemClicked.connect(self._on_item_clicked)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemExpanded.connect(self._on_group_toggled)
        self.itemCollapsed.connect(self._on_group_toggled)

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
        for group in (self._group_branches, self._group_local, self._group_remote):
            group.setData(0, _ROLE_IS_GROUP, True)
        self._group_branches.addChild(self._group_local)
        self._group_branches.addChild(self._group_remote)
        self.addTopLevelItem(self._group_branches)

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
        self._group_tags.setData(0, _ROLE_IS_GROUP, True)
        self.addTopLevelItem(self._group_tags)
        for tag in self._vm.tags():
            item = QTreeWidgetItem([tag.name])
            item.setData(0, _ROLE_KIND, _KIND_TAG)
            item.setData(0, _ROLE_NAME, tag.name)
            self._group_tags.addChild(item)
        if not self._group_tags.childCount():
            self._group_tags.setDisabled(True)

        self._group_stash = QTreeWidgetItem([_GROUP_STASH])
        self._group_stash.setData(0, _ROLE_IS_GROUP, True)
        self.addTopLevelItem(self._group_stash)
        for entry in self._vm.stash_list():
            label = f"stash@{{{entry.index}}}: {entry.message}"
            item = QTreeWidgetItem([label])
            item.setData(0, _ROLE_KIND, _KIND_STASH)
            item.setData(0, _ROLE_NAME, str(entry.index))
            self._group_stash.addChild(item)
        if not self._group_stash.childCount():
            self._group_stash.setDisabled(True)

        # Set expansion state and chevron icons. Order matters: each
        # group must already have its children attached, otherwise
        # ``_set_expand_icon`` would treat the row as a leaf and
        # clear the icon. ``setExpanded`` also fires ``itemExpanded``,
        # which keeps the chevron in sync for the groups that actually
        # expand; the explicit calls below cover the groups that stay
        # collapsed (``Remote`` / ``Tags`` / ``Stash``).
        self._group_branches.setExpanded(True)
        self._group_local.setExpanded(True)
        self._set_expand_icon(self._group_branches)
        self._set_expand_icon(self._group_local)
        self._set_expand_icon(self._group_remote)
        self._set_expand_icon(self._group_tags)
        self._set_expand_icon(self._group_stash)

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

    def _set_expand_icon(self, item: QTreeWidgetItem) -> None:
        """Set the chevron icon on a group item from its expansion state.

        Only rows that carry the ``_ROLE_IS_GROUP`` marker are touched
        — leaves and the placeholder are left alone, even if the group
        is currently empty. The chevron uses the platform's standard
        arrow pixmaps so it follows the OS look and reads well on the
        dark surface even when Qt's default branch indicator is barely
        visible. The pixmap is then tinted with the theme's text color
        so the chevron blends in with the row label.
        """
        if item.data(0, _ROLE_IS_GROUP) is not True:
            return
        item.setIcon(0, self._tint_chevron(item.isExpanded()))

    def _tint_chevron(self, expanded: bool) -> QIcon:
        """Return a chevron icon tinted with the theme's text color.

        The standard pixmap (SP_ArrowRight / SP_ArrowDown) is drawn in
        the platform's icon color, which on Windows stays near-black
        even when a dark QSS is applied. We walk the source's alpha
        channel and replace every non-transparent pixel with the
        theme's text color, so the chevron reads like the row label
        next to it. A direct pixel walk is more reliable than a
        ``QPainter`` composition pass on the offscreen Qt platform
        used by the test suite.
        """
        standard = (
            QStyle.StandardPixmap.SP_ArrowDown
            if expanded
            else QStyle.StandardPixmap.SP_ArrowRight
        )
        source = self.style().standardPixmap(standard)
        if source.isNull():
            return QIcon(source)

        image = source.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        text_color = QColor(DARK_THEME.text)
        r, g, b = text_color.red(), text_color.green(), text_color.blue()

        for y in range(image.height()):
            for x in range(image.width()):
                alpha = (image.pixel(x, y) >> 24) & 0xFF
                if alpha:
                    image.setPixel(x, y, (alpha << 24) | (r << 16) | (g << 8) | b)

        return QIcon(QPixmap.fromImage(image))

    def _on_group_toggled(self, item: QTreeWidgetItem) -> None:
        """Refresh the chevron when a group row is expanded or collapsed.

        Only group rows carry the ``_ROLE_IS_GROUP`` marker; leaves
        and the placeholder do not, so the icon update runs only on
        the rows that actually need it.
        """
        if item.data(0, _ROLE_IS_GROUP) is True:
            self._set_expand_icon(item)

    # ----- user actions ------------------------------------------------

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Double-click: local branch → checkout; remote → fetch + checkout; tag → create branch."""
        kind = item.data(0, _ROLE_KIND)
        name = item.data(0, _ROLE_NAME)
        if kind is None:
            # Group header (Branches / Local / Remote / Tags / Stash):
            # double-click toggles the expanded state. We can't rely on
            # Qt's default expand-on-double-click because it is disabled
            # in ``__init__`` (so the row text click below is the
            # checkout trigger for leaves).
            item.setExpanded(not item.isExpanded())
            return
        if kind == _KIND_LOCAL_BRANCH and name:
            self._main_vm.checkout_branch(name)
        elif kind == _KIND_REMOTE_BRANCH and name:
            self._main_vm.fetch_and_checkout_remote_branch(name)
        elif kind == _KIND_TAG and name:
            self._main_vm.create_branch(name)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Single-click on a group header toggles its expanded state.

        Leaves (branches / tags / stash entries) carry a ``_ROLE_KIND``
        value; group headers do not — that is the discriminator. With
        ``setExpandsOnDoubleClick(False)`` Qt will not expand a row on
        double-click, and the tiny triangle indicator on the left is
        not a discoverable affordance. This makes the whole row
        clickable to toggle, matching GitKraken's behaviour.

        Note: a double-click fires ``itemClicked`` twice (once per
        click) before ``itemDoubleClicked`` fires. Two single-click
        toggles cancel out, so a double-click on a group header ends
        up as a single toggle via the ``itemDoubleClicked`` handler
        above — net effect: the state flips once, as expected.
        """
        if item.data(0, _ROLE_KIND) is None:
            item.setExpanded(not item.isExpanded())

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
            actions.extend(self._local_branch_actions(name))
        elif kind == _KIND_REMOTE_BRANCH:
            create_from = QAction(f"Create Branch from {name}…", self)
            create_from.triggered.connect(lambda: self._prompt_create_branch(from_name=name))
            actions.append(create_from)
            # ``name`` is like ``origin/main`` — the leading segment is
            # the remote. The fetch is per-remote, not per-branch.
            remote_name = self._vm.get_remote_for_branch(name)
            if remote_name:
                fetch_action = QAction(f"Fetch from {remote_name}", self)
                fetch_action.triggered.connect(
                    lambda: self._main_vm.fetch_changes(remote_name),
                )
                actions.append(fetch_action)
        elif kind == _KIND_TAG:
            create_from = QAction(f"Create Branch from {name}…", self)
            create_from.triggered.connect(lambda: self._prompt_create_branch(from_name=name))
            actions.append(create_from)
            actions.extend(self._tag_cherry_pick_actions(name))
        elif kind == _KIND_STASH:
            apply = QAction("Apply (Stage 7)", self)
            apply.setEnabled(False)
            actions.append(apply)
        return actions

    def _local_branch_actions(self, name: str) -> list[QAction]:
        """Build the context-menu actions for a local branch leaf."""
        actions: list[QAction] = []
        checkout = QAction("Checkout", self)
        checkout.triggered.connect(lambda: self._main_vm.checkout_branch(name))
        actions.append(checkout)
        actions.extend(self._merge_rebase_against_current(name))
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
        return actions

    def _merge_rebase_against_current(self, name: str) -> list[QAction]:
        """Add Merge / Rebase / Cherry-pick actions that target the current HEAD.

        If ``name`` *is* the current branch the actions are added but
        disabled — merging a branch into itself is a no-op. Cherry-pick
        is a placeholder for now; the real dialog is in Stage 5+.
        """
        actions: list[QAction] = []
        mgr = self._main_vm.repository_manager()
        is_current = (
            mgr is not None
            and not mgr.repo.head_is_unborn
            and mgr.repo.head.shorthand == name
        )
        merge = QAction(f"Merge {name} into current…", self)
        merge.triggered.connect(
            lambda: self._main_vm.merge_branch(name, target=self._current_branch_name()),
        )
        merge.setEnabled(not is_current)
        actions.append(merge)
        rebase = QAction(f"Rebase {name} onto current…", self)
        rebase.triggered.connect(
            lambda: self._rebase_source_onto_target(name, self._current_branch_name()),
        )
        rebase.setEnabled(not is_current)
        actions.append(rebase)
        return actions

    def _tag_cherry_pick_actions(self, tag_name: str) -> list[QAction]:
        actions: list[QAction] = []
        cherry = QAction(f"Cherry-pick {tag_name}…", self)
        cherry.triggered.connect(lambda: self._prompt_cherry_pick(label=tag_name))
        actions.append(cherry)
        return actions

    def _current_branch_name(self) -> str:
        mgr = self._main_vm.repository_manager()
        if mgr is None or mgr.repo.head_is_unborn:
            return ""
        return mgr.repo.head.shorthand

    def _prompt_cherry_pick(self, label: str) -> None:
        """Ask for a SHA and dispatch :meth:`MainViewModel.cherry_pick`."""
        sha, ok = QInputDialog.getText(
            self,
            "Cherry-pick",
            f"Cherry-pick commit SHA for {label}:",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return
        sha = sha.strip()
        if not sha:
            return
        self._main_vm.cherry_pick(sha)

    # ----- drag-and-drop ---------------------------------------------

    def mimeData(self, items):  # noqa: ANN001, N802 - Qt override + return type
        """Return a :class:`QMimeData` whose text is the bare branch name.

        The default :class:`QTreeWidget` implementation uses the
        item's display text — which for the current branch is
        ``"name  (HEAD)"``. Overriding here gives the drop handler
        a clean identifier to work with.
        """
        data = super().mimeData(items)
        if not items or self._group_local is None:
            return data
        item = items[0]
        if item.parent() is not self._group_local:
            return data
        name = item.data(0, _ROLE_NAME)
        if isinstance(data, QMimeData) and name:
            data.setText(str(name))
        return data

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt override
        if not event.mimeData().hasText():
            super().dropEvent(event)
            return
        source_name = event.mimeData().text()
        target_item = self.itemAt(event.position().toPoint())
        actions = self._on_drop(source_name, target_item)
        if not actions:
            event.ignore()
            return
        menu = QMenu(self)
        for action in actions:
            menu.addAction(action)
        menu.exec(event.position().toPoint())
        event.acceptProposedAction()

    def _on_drop(
        self,
        source_name: str,
        target_item: QTreeWidgetItem | None,
    ) -> list[QAction]:
        """Return the menu actions a drop would show, or ``[]`` to ignore.

        Exposed (single-underscore) so tests can verify the menu
        contents without running a real ``QMenu.exec()``. The drop
        itself is filtered:

        * No target or invalid target → ``[]`` (ignore).
        * ``source == target`` → ``[]`` (merging a branch into itself).
        * Target is not a local branch → ``[]`` (we only allow
          dropping on local branches for now).
        """
        if not source_name or target_item is None:
            return []
        target_kind = target_item.data(0, _ROLE_KIND)
        target_name = target_item.data(0, _ROLE_NAME)
        if not target_kind or not target_name:
            return []
        if source_name == target_name:
            return []
        if target_kind != _KIND_LOCAL_BRANCH:
            return []
        return self._drop_actions(source_name, target_name)

    def _drop_actions(self, source: str, target: str) -> list[QAction]:
        """Build the list of actions for a drop on ``target`` of ``source``."""
        actions: list[QAction] = []
        merge = QAction(f"Merge {source} into {target}", self)
        merge.triggered.connect(
            lambda: self._main_vm.merge_branch(source, target=target),
        )
        actions.append(merge)
        rebase = QAction(f"Rebase {source} onto {target}", self)
        rebase.triggered.connect(
            lambda: self._rebase_source_onto_target(source, target),
        )
        actions.append(rebase)
        return actions

    def _rebase_source_onto_target(self, source: str, target: str) -> None:
        """Issue the two-command sequence: checkout ``source`` then rebase onto ``target``.

        The two commands are pushed onto the undo stack separately
        so the user can undo each step independently. If the user is
        already on ``source`` the checkout is a no-op (the VM still
        calls into the processor, which is fine). If checkout fails
        (e.g. dirty worktree), rebase is skipped.
        """
        mgr = self._main_vm.repository_manager()
        current = None
        if mgr is not None and not mgr.repo.head_is_unborn:
            current = mgr.repo.head.shorthand
        if current != source:
            ok = self._main_vm.checkout_branch(source)
            if not ok:
                return
        self._main_vm.rebase_branch(target)

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
