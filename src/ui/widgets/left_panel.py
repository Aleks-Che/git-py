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

# Custom MIME type carrying the discriminator of a drag source.
# ``application/x-git-py-branch-kind`` holds the bare ``_KIND_*`` value
# so the drop handler knows whether the drag came from a local or
# remote branch row (the two paths produce different menus).
_BRANCH_KIND_MIME = "application/x-git-py-branch-kind"

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

        # Hide the ``Remote`` group entirely when the suppression filter
        # emptied it (every remote matches a local). Otherwise the user
        # sees a visible-but-empty group header — which reads as "remote
        # branches are still listed" and contradicts the intended single
        # local entry.
        if self._group_remote.childCount() == 0:
            self._group_remote.setHidden(True)

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
        """Enable drag on local and remote-branch leaves; the drop handler is the Stage 5 stub.

        Both groups get :attr:`Qt.ItemFlag.ItemIsDragEnabled` because
        the user may want to drag a remote-tracking branch onto a
        local branch just like they would in the graph chip column.
        The drop handler is the gate that decides whether the source
        + target combination is meaningful (e.g. ``feature`` →
        ``main`` produces a merge/rebase menu; ``origin/main`` →
        ``main`` produces a fetch+checkout dialog).
        """
        # QTreeWidget is drag-enabled wholesale when any item has
        # ItemIsDragEnabled. We toggle that flag on individual items in
        # the local- and remote-branch groups.
        groups = [
            g for g in (self._group_local, self._group_remote)
            if g is not None
        ]
        for group in groups:
            for i in range(group.childCount()):
                leaf = group.child(i)
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
        """Double-click a local branch, remote branch, or tag.

        Double-clicking a remote-tracking branch is the conventional
        "switch to the remote" gesture. When a local tracking
        branch already exists, the user almost always wants to
        abandon any unpushed local work (e.g. an unmerged merge
        commit) and land on whatever the remote is currently
        pointing at — that is the destructive "Reset Local to
        Here?" action.  The local panel pops a confirmation dialog
        for that path because ``reset --hard`` cannot be undone
        through the normal ``CommandProcessor`` undo stack.

        When the local branch does not exist yet, there is nothing
        to lose, so the dialog is skipped and the
        :meth:`MainViewModel.fetch_and_checkout_remote_branch`
        path is used directly (fetch + create + checkout).
        """
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
            self._handle_remote_double_click(name)
        elif kind == _KIND_TAG and name:
            self._main_vm.create_branch(name)

    def _handle_remote_double_click(self, remote_branch_name: str) -> None:
        """Switch to ``remote_branch_name`` — confirm if a local branch exists.

        Splits out the double-click dispatch from
        :meth:`_on_double_clicked` so the test suite can target the
        confirmation flow without going through the whole
        ``itemDoubleClicked`` event channel.  The
        :class:`QMessageBox` is the conventional "are you sure"
        dialog for destructive actions: it shows the user the
        name of the local branch that will be reset and a clear
        statement of the consequence, with ``No`` as the default
        button so an accidental Enter does not discard work.
        """
        # ``origin/feature`` → local branch is ``feature``.
        local_name = remote_branch_name.split("/", 1)[1]
        if not self._local_branch_exists(local_name):
            # No local work to lose — just create it.
            self._main_vm.fetch_and_checkout_remote_branch(remote_branch_name)
            return
        from PySide6.QtWidgets import QMessageBox
        confirm = QMessageBox.question(
            self,
            "Reset Local Branch",
            f"Reset local '{local_name}' to match the remote?\n\n"
            f"This will discard any unpushed commits on '{local_name}' "
            f"(including the merge that is not yet on the remote). "
            f"Working-tree changes will also be lost.\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._main_vm.reset_local_branch_to_remote(remote_branch_name)

    def _local_branch_exists(self, name: str) -> bool:
        """Return ``True`` if a local branch named ``name`` exists.

        Used by :meth:`_handle_remote_double_click` to decide
        whether the destructive reset path needs a confirmation
        dialog.  The check is name-only — we do not verify the
        upstream config because a local branch with the right
        name is what the user would reset regardless of where its
        upstream points.
        """
        mgr = self._main_vm.repository_manager()
        if mgr is None:
            return False
        return any(b.name == name and not b.is_remote for b in mgr.branches)

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

        Single-click on a **leaf** (local/remote branch or tag) drives
        the graph: the commit the ref points at becomes the selected
        commit in the graph and the graph scrolls to bring it into
        view (both vertically and horizontally on the graph column).
        The double-click handler keeps its old verb (checkout /
        create branch) — the scroll-then-act sequence is what
        GitKraken does. Stash leaves are intentionally left alone on
        single-click: the user double-clicks a stash entry to open
        the apply / pop / drop context menu via the standard
        double-click path.
        """
        kind = item.data(0, _ROLE_KIND)
        if kind is None:
            item.setExpanded(not item.isExpanded())
            return
        if kind in (_KIND_LOCAL_BRANCH, _KIND_REMOTE_BRANCH, _KIND_TAG):
            self._focus_ref_on_graph(kind=kind, name=item.data(0, _ROLE_NAME))

    def _focus_ref_on_graph(self, *, kind: str, name: str | None) -> None:
        """Select the commit pointed to by *name* on the graph and scroll to it.

        Resolves *name* against the :class:`BranchPanelViewModel`
        snapshot to find the target SHA, then asks the
        :class:`MainViewModel` to make it the selected commit and the
        :class:`GraphViewModel` to scroll the graph view to it. The
        two calls go through different paths on purpose: the
        selection drives the right panel (and the selection ring on
        the graph node), the scroll moves the viewport to the commit
        row. Stash entries are filtered out by the caller.

        No-op when the ref cannot be resolved (e.g. the snapshot is
        stale) or the target SHA is empty — the right panel and the
        graph selection would otherwise be set to ``None`` and the
        current selection cleared, which is surprising for a single
        misclick on an outdated row.
        """
        if not name:
            return
        sha = self._resolve_ref_sha(kind=kind, name=name)
        if not sha:
            return
        self._main_vm.set_selected_commit(sha)
        self._main_vm.graph_view_model().scroll_to_commit(sha)

    def _resolve_ref_sha(self, *, kind: str, name: str) -> str | None:
        """Look up the commit SHA a branch / tag leaf points at.

        Branches and tags both expose ``target_sha`` on the
        :class:`BranchInfo` / :class:`TagInfo` dataclasses, so the
        lookup is uniform. Returns ``None`` if *name* is no longer
        in the panel's snapshot — that happens after a ``refresh()``
        races with a click and the user clicked the old row.
        """
        if kind == _KIND_LOCAL_BRANCH:
            for branch in self._vm.local_branches():
                if branch.name == name:
                    return branch.target_sha
        elif kind == _KIND_REMOTE_BRANCH:
            for branch in self._vm.remote_branches():
                if branch.name == name:
                    return branch.target_sha
        elif kind == _KIND_TAG:
            for tag in self._vm.tags():
                if tag.name == name:
                    return tag.target_sha
        return None

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
            actions.extend(self._remote_branch_actions(name))
        elif kind == _KIND_TAG:
            create_from = QAction(f"Create Branch from {name}…", self)
            create_from.triggered.connect(lambda: self._prompt_create_branch(from_name=name))
            actions.append(create_from)
            actions.extend(self._tag_cherry_pick_actions(name))
        elif kind == _KIND_STASH:
            apply = QAction("Apply Stash", self)
            apply.triggered.connect(
                lambda checked=False, i=int(name): self._apply_stash(i),
            )
            actions.append(apply)
            pop = QAction("Pop Stash", self)
            pop.triggered.connect(
                lambda checked=False, i=int(name): self._pop_stash(i),
            )
            actions.append(pop)
            drop = QAction("Drop Stash", self)
            drop.triggered.connect(
                lambda checked=False, i=int(name): self._drop_stash(i),
            )
            actions.append(drop)
        return actions

    def _local_branch_actions(self, name: str) -> list[QAction]:
        """Build the context-menu actions for a local branch leaf."""
        actions: list[QAction] = []
        mgr = self._main_vm.repository_manager()
        is_current = (
            mgr is not None
            and not mgr.repo.head_is_unborn
            and mgr.repo.head.shorthand == name
        )
        upstream_name = self._get_upstream_remote_name(name)
        target_sha = self._resolve_local_branch_sha(name)

        # ----- push / pull (current branch only) ------------------------

        pull = QAction("Pull", self)
        pull.triggered.connect(lambda: self._main_vm.pull_changes())
        pull.setEnabled(is_current)
        actions.append(pull)

        push = QAction("Push", self)
        push.triggered.connect(lambda: self._main_vm.push_changes())
        push.setEnabled(is_current)
        actions.append(push)

        # ----- checkout / merge / rebase --------------------------------

        checkout = QAction("Checkout", self)
        checkout.triggered.connect(lambda: self._main_vm.checkout_branch(name))
        actions.append(checkout)

        actions.extend(self._merge_rebase_against_current(name))

        # ``Merge into…`` / ``Rebase onto…`` submenus mirroring the
        # graph drag-and-drop UX: a one-shot target picker so the user
        # does not have to first ``checkout`` the target branch.
        merge_into = self._merge_into_submenu(name, _KIND_LOCAL_BRANCH)
        if merge_into is not None:
            actions.append(merge_into)
        rebase_onto = self._merge_into_submenu(
            name, _KIND_LOCAL_BRANCH, rebase=True,
        )
        if rebase_onto is not None:
            actions.append(rebase_onto)

        actions.append(QAction(self))
        actions[-1].setSeparator(True)

        # ----- create / rename / delete ---------------------------------

        create_from = QAction(f"Create Branch from {name}…", self)
        create_from.triggered.connect(lambda: self._prompt_create_branch(from_name=name))
        actions.append(create_from)

        rename = QAction(f"Rename {name}…", self)
        rename.triggered.connect(lambda: self._prompt_rename(old_name=name))
        actions.append(rename)

        delete = QAction(f"Delete {name}", self)
        delete.triggered.connect(lambda: self._prompt_delete(name))
        actions.append(delete)

        if upstream_name:
            delete_remote = QAction(f"Delete {upstream_name}", self)
            delete_remote.triggered.connect(
                lambda checked=False, u=upstream_name: self._main_vm.delete_remote_branch(u),
            )
            actions.append(delete_remote)

            delete_both = QAction(f"Delete {name} and {upstream_name}", self)
            delete_both.triggered.connect(
                lambda checked=False, n=name, u=upstream_name: (
                    self._main_vm.delete_local_and_remote_branch(n, u)
                ),
            )
            actions.append(delete_both)

        actions.append(QAction(self))
        actions[-1].setSeparator(True)

        # ----- copy -----------------------------------------------------

        copy_name = QAction("Copy branch name", self)
        copy_name.triggered.connect(lambda: self._main_vm.copy_to_clipboard(name))
        actions.append(copy_name)

        if target_sha:
            copy_sha = QAction("Copy commit sha", self)
            copy_sha.triggered.connect(lambda: self._main_vm.copy_to_clipboard(target_sha))
            actions.append(copy_sha)

        actions.append(QAction(self))
        actions[-1].setSeparator(True)

        # ----- tag ------------------------------------------------------

        if target_sha:
            create_tag = QAction("Create tag here…", self)
            create_tag.triggered.connect(
                lambda checked=False, s=target_sha, n=name: (
                    self._prompt_create_tag(annotated=False, sha=s, from_name=n)
                ),
            )
            actions.append(create_tag)

            create_annotated = QAction("Create annotated tag here…", self)
            create_annotated.triggered.connect(
                lambda checked=False, s=target_sha, n=name: (
                    self._prompt_create_tag(annotated=True, sha=s, from_name=n)
                ),
            )
            actions.append(create_annotated)

        return actions

    def _remote_branch_actions(self, name: str) -> list[QAction]:
        """Build the context-menu actions for a remote branch leaf.

        ``name`` is like ``origin/main``. The leading segment is the
        remote name; the rest is the branch name on the remote.
        """
        actions: list[QAction] = []
        remote_name = self._vm.get_remote_for_branch(name)
        # Resolve the SHA of this remote-tracking branch.
        target_sha = None
        for b in self._vm.remote_branches():
            if b.name == name:
                target_sha = b.target_sha
                break

        # ----- checkout (create local tracking branch) ------------------

        checkout = QAction(f"Checkout {name} as local branch", self)
        checkout.triggered.connect(
            lambda n=name: self._handle_remote_double_click(n),
        )
        actions.append(checkout)

        # ``Merge into…`` / ``Rebase onto…`` submenus so the user can
        # merge a remote-tracking tip straight into any local branch
        # without first checking it out. :meth:`_merge_drop` /
        # :meth:`_rebase_drop` transparently run fetch+checkout for
        # the remote source before the actual merge/rebase.
        merge_into = self._merge_into_submenu(name, _KIND_REMOTE_BRANCH)
        if merge_into is not None:
            actions.append(merge_into)
        rebase_onto = self._merge_into_submenu(
            name, _KIND_REMOTE_BRANCH, rebase=True,
        )
        if rebase_onto is not None:
            actions.append(rebase_onto)

        actions.append(QAction(self))
        actions[-1].setSeparator(True)

        # ----- create local branch from here ----------------------------

        create_from = QAction(f"Create Branch from {name}…", self)
        create_from.triggered.connect(lambda: self._prompt_create_branch(from_name=name))
        actions.append(create_from)

        # ----- delete remote branch -------------------------------------

        delete_remote = QAction(f"Delete {name}", self)
        delete_remote.triggered.connect(
            lambda checked=False, u=name: self._main_vm.delete_remote_branch(u),
        )
        actions.append(delete_remote)

        actions.append(QAction(self))
        actions[-1].setSeparator(True)

        # ----- copy -----------------------------------------------------

        copy_name = QAction("Copy branch name", self)
        copy_name.triggered.connect(lambda: self._main_vm.copy_to_clipboard(name))
        actions.append(copy_name)

        if target_sha:
            copy_sha = QAction("Copy commit sha", self)
            copy_sha.triggered.connect(lambda: self._main_vm.copy_to_clipboard(target_sha))
            actions.append(copy_sha)

        actions.append(QAction(self))
        actions[-1].setSeparator(True)

        # ----- fetch ----------------------------------------------------

        if remote_name:
            fetch_action = QAction(f"Fetch from {remote_name}", self)
            fetch_action.triggered.connect(
                lambda checked=False, r=remote_name: self._main_vm.fetch_changes(r),
            )
            actions.append(fetch_action)

        return actions

    def _merge_rebase_against_current(self, name: str) -> list[QAction]:
        """Add Merge / Rebase / Cherry-pick actions that target the current HEAD.

        If ``name`` *is* the current branch the actions are added but
        disabled — merging a branch into itself is a no-op. Cherry-pick
        is a placeholder for now; the real dialog is in Stage 5+.

        The merge action passes ``no_ff=True`` so the merge commit
        is always visible in the graph, even on a fast-forward.
        The user explicitly asked for a merge through the context
        menu; the history should reflect that explicitly instead of
        silently moving the ref.
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
            lambda: self._main_vm.merge_branch(
                name, target=self._current_branch_name(), no_ff=True,
            ),
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

    def _merge_into_submenu(
        self,
        source_name: str,
        source_kind: str = _KIND_LOCAL_BRANCH,
        rebase: bool = False,
    ) -> QAction | None:
        """Build a ``QAction`` whose submenu merges ``source_name`` into each local branch.

        Returns ``None`` if there is no other local branch to merge
        *into* (the one branch repo never gets a submenu — there's
        nothing to choose from). The returned action carries a
        :class:`QMenu` set via :meth:`QAction.setMenu` so the parent
        context menu (built in :meth:`_on_context_menu`) shows a
        proper submenu arrow.

        ``source_kind`` matches the drag-MIME payload and supports
        both local and remote sources. The actual normalisation
        (fetch + checkout tracking branch for remote sources) is
        deferred to the click handler so the menu still builds even
        when the network is offline.
        """
        mgr = self._main_vm.repository_manager()
        if mgr is None:
            return None
        local_names = sorted(
            b.name for b in mgr.branches if not b.is_remote
        )
        candidates = [n for n in local_names if n != source_name]
        if not candidates:
            return None
        label = (
            f"Rebase {source_name} onto..." if rebase
            else f"Merge {source_name} into..."
        )
        parent_action = QAction(label, self)
        submenu = QMenu(self)
        verb = "rebase_drop" if rebase else "merge_drop"
        for target in candidates:
            action = QAction(target, submenu)
            action.triggered.connect(
                lambda checked=False, t=target, v=verb, s=source_name, k=source_kind: (
                    self._invoke_drop_action(v, s, t, k)
                ),
            )
            submenu.addAction(action)
        parent_action.setMenu(submenu)
        return parent_action

    def _invoke_drop_action(
        self,
        verb: str,
        source: str,
        target: str,
        source_kind: str,
    ) -> None:
        """Dispatch a submenu selection to the matching drop helper.

        ``verb`` is one of ``"merge_drop"`` / ``"rebase_drop"`` — the
        routing is just two calls but keeping the verb string around
        makes the menu builder above easier to read.
        """
        if verb == "merge_drop":
            self._merge_drop(source, target, source_kind)
        elif verb == "rebase_drop":
            self._rebase_drop(source, target, source_kind)

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

        Both local and remote branches are eligible as drag sources;
        the source kind discriminator is carried in a custom MIME
        (``_BRANCH_KIND_MIME``) so :meth:`_on_drop` can tell whether
        the source is a local branch (drag → merge/rebase menu) or a
        remote-tracking ref (drag → fetch+merge dialog).

        The plain-text field still receives the bare branch name so
        downstream consumers that only know how to read text (e.g.
        another ``QTreeWidget`` accepting the same drag) keep working.
        """
        data = super().mimeData(items)
        if not items:
            return data
        item = items[0]
        kind = item.data(0, _ROLE_KIND)
        if kind not in (_KIND_LOCAL_BRANCH, _KIND_REMOTE_BRANCH):
            return data
        name = item.data(0, _ROLE_NAME)
        if isinstance(data, QMimeData) and name:
            data.setText(str(name))
            data.setData(_BRANCH_KIND_MIME, str(kind).encode("utf-8"))
        return data

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasText() and mime.hasFormat(_BRANCH_KIND_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasText() and mime.hasFormat(_BRANCH_KIND_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt override
        mime = event.mimeData()
        if not mime.hasText() or not mime.hasFormat(_BRANCH_KIND_MIME):
            super().dropEvent(event)
            return
        source_name = mime.text()
        source_kind = bytes(mime.data(_BRANCH_KIND_MIME)).decode("utf-8")
        target_item = self.itemAt(event.position().toPoint())
        actions = self._on_drop(source_name, source_kind, target_item)
        if not actions:
            event.ignore()
            return
        menu = QMenu(self)
        for action in actions:
            menu.addAction(action)
        # H15: ``QMenu.exec`` expects a *global* screen position; pass
        # the drop point in viewport-relative coordinates and map it
        # up to global screen coordinates so the menu actually
        # appears under the cursor.
        menu.exec(self.viewport().mapToGlobal(event.position().toPoint()))
        event.acceptProposedAction()

    def _on_drop(
        self,
        source_name: str,
        source_kind: str,
        target_item: QTreeWidgetItem | None,
    ) -> list[QAction]:
        """Return the menu actions a drop would show, or ``[]`` to ignore.

        Exposed (single-underscore) so tests can verify the menu
        contents without running a real ``QMenu.exec()``. The drop
        itself is filtered:

        * No target or invalid target → ``[]`` (ignore).
        * ``source == target`` → ``[]`` (merging a branch into itself).
        * Target is not a local branch → ``[]`` (we only allow
          dropping on local branches — merge/rebase rebases history
          onto a working-tree branch).

        ``source_kind`` (``"local_branch"`` or ``"remote_branch"``)
        is read from the :data:`_BRANCH_KIND_MIME` payload that
        :meth:`mimeData` attaches; the resulting menu is the same in
        both cases — a fetch helper transparently takes care of
        upgrading a remote source to its local tracking branch.
        """
        if not source_name or target_item is None:
            return []
        target_kind = target_item.data(0, _ROLE_KIND)
        target_name = target_item.data(0, _ROLE_NAME)
        if not target_kind or not target_name:
            return []
        if source_kind not in (_KIND_LOCAL_BRANCH, _KIND_REMOTE_BRANCH):
            return []
        if source_name == target_name:
            return []
        if target_kind != _KIND_LOCAL_BRANCH:
            return []
        return self._drop_actions(source_name, target_name, source_kind)

    def _drop_actions(
        self,
        source: str,
        target: str,
        source_kind: str = _KIND_LOCAL_BRANCH,
    ) -> list[QAction]:
        """Build the list of actions for a drop on ``target`` of ``source``.

        The merge action passes ``no_ff=True`` so the merge commit
        is always visible in the graph, even on a fast-forward.
        The user asked for a merge by dragging one branch onto
        another; the history should reflect that explicitly.

        ``source_kind`` carries the discriminator (``local_branch`` /
        ``remote_branch``) from the drag source item — needed so a
        remote source is first fetched into a local tracking branch
        before any merge or rebase is attempted. The merge/rebase
        actions themselves only ever speak local branch names.
        """
        actions: list[QAction] = []
        merge = QAction(f"Merge {source} into {target}", self)
        # ``QAction.triggered`` emits a single ``bool`` argument (the
        # ``checked`` flag); accepting it positionally with a default
        # of ``False`` lets the closure stay pure (no ``checked``
        # leak into our source/target args).
        merge.triggered.connect(
            lambda checked=False, s=source, t=target, k=source_kind: (
                self._merge_drop(s, t, k)
            ),
        )
        actions.append(merge)
        rebase = QAction(f"Rebase {source} onto {target}", self)
        rebase.triggered.connect(
            lambda checked=False, s=source, t=target, k=source_kind: (
                self._rebase_drop(s, t, k)
            ),
        )
        actions.append(rebase)
        return actions

    def _merge_drop(self, source: str, target: str, source_kind: str) -> None:
        """Run a drop-initiated merge, normalising a remote source first.

        The actual ``merge_branch(source, target)`` call needs a local
        branch — merging ``origin/main`` directly into ``main`` is not
        a meaningful ref in pygit2's terms (remote-tracking refs are
        read-only mirrors). For a remote source we first fetch+create
        a local tracking branch via
        :meth:`MainViewModel.fetch_and_checkout_remote_branch` and
        only then merge with ``no_ff=True``. Failures bubble up
        through ``error_occurred`` as usual; we don't gate this on a
        dialog because dropping a remote branch is an explicit
        ``"merge this remote tip into my local branch"`` action.
        """
        local_source = source
        if source_kind == _KIND_REMOTE_BRANCH:
            self._main_vm.fetch_and_checkout_remote_branch(source)
            local_source = source.split("/", 1)[1]
        self._main_vm.merge_branch(local_source, target=target, no_ff=True)

    def _rebase_drop(self, source: str, target: str, source_kind: str) -> None:
        """Run a drop-initiated rebase, normalising a remote source first.

        Same normalisation logic as :meth:`_merge_drop` — for a
        remote source we fetch and create the local tracking branch
        first. After that the existing
        :meth:`_rebase_source_onto_target` helper does the
        checkout + rebase sequence as a two-step undo stack.
        """
        local_source = source
        if source_kind == _KIND_REMOTE_BRANCH:
            self._main_vm.fetch_and_checkout_remote_branch(source)
            local_source = source.split("/", 1)[1]
        self._rebase_source_onto_target(local_source, target)

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

    # ----- helpers: branch metadata -----------------------------------

    def _get_upstream_remote_name(self, local_name: str) -> str | None:
        """Return the remote-tracking name for *local_name*, e.g. ``origin/main``.

        Returns ``None`` when the branch has no upstream configured or
        the upstream is not a remote-tracking reference (e.g. another
        local branch).
        """
        mgr = self._main_vm.repository_manager()
        if mgr is None:
            return None
        try:
            branch = mgr.repo.lookup_branch(local_name)
            if branch is None:
                return None
            upstream = branch.upstream_name
            if upstream and upstream.startswith("refs/remotes/"):
                return upstream[len("refs/remotes/"):]
        except Exception:
            pass
        return None

    def _resolve_local_branch_sha(self, name: str) -> str | None:
        """Look up the commit SHA a local branch points at."""
        for branch in self._vm.local_branches():
            if branch.name == name:
                return branch.target_sha
        return None

    # ----- prompts (small dialogs) ------------------------------------

    def _prompt_create_tag(self, *, annotated: bool, sha: str, from_name: str) -> None:
        """Ask for a tag name; create it at *sha* (lightweight or annotated)."""
        tag_name, ok = QInputDialog.getText(
            self,
            "Create Annotated Tag" if annotated else "Create Tag",
            f"Tag name (at {from_name!r}):",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return
        tag_name = tag_name.strip()
        if not tag_name:
            return
        message = None
        if annotated:
            msg, ok = QInputDialog.getText(
                self,
                "Tag Message",
                f"Message for annotated tag {tag_name!r}:",
                QLineEdit.EchoMode.Normal,
                "",
            )
            if not ok:
                return
            message = msg.strip() or None
        self._main_vm.create_tag(tag_name, sha, message=message)

    # ----- stash verb delegations --------------------------------------

    def _apply_stash(self, index: int) -> None:
        """Apply the stash at *index* without dropping it.

        Defers to :meth:`MainViewModel.stash_apply` so the operation
        runs through the ``CommandProcessor`` (Undo / Redo work) and
        the error path goes through the normal VM error signal.
        """
        if not self._has_stash(index):
            return
        self._main_vm.stash_apply(index)

    def _pop_stash(self, index: int) -> None:
        """Apply and drop the stash at *index*."""
        if not self._has_stash(index):
            return
        self._main_vm.stash_pop(index)

    def _drop_stash(self, index: int) -> None:
        """Drop the stash at *index* after a confirm dialog."""
        if not self._has_stash(index):
            return
        confirm = QMessageBox.question(
            self,
            "Drop Stash",
            f"Drop stash@{{{index}}}? This cannot be easily undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._main_vm.stash_drop(index)

    def _has_stash(self, index: int) -> bool:
        """Return ``True`` if a stash entry at *index* still exists.

        The stash list is rebuilt on every :attr:`references_changed`
        emission; between the context menu opening and the action
        firing the user could have triggered another stash op (a
        remote-side push trigger, a script, ...) that renumbers the
        entries. We re-read the live list instead of trusting the
        index captured when the menu was built.
        """
        if self._main_vm.repository_manager() is None:
            return False
        try:
            stash_list = self._main_vm.repository_manager().stash_list
        except GitError:
            return False
        return 0 <= index < len(stash_list)


__all__ = ["LeftPanel"]
