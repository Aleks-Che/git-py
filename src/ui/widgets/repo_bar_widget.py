"""Repository tab bar widget — tabs for each open repository + a ``+`` button.

Uses ``QTabBar.setTabButton`` with a lightweight ``QWidget`` that
paints its own ``×`` glyph, completely bypassing ``QPushButton`` style
padding.  The glyph is hidden by default and revealed when the
mouse hovers over the parent tab.

Right-click on a tab opens a context menu with five actions:

* **Show repo folder** — open the repo root in the OS file explorer.
* **Copy repo path** — copy the repo root to the clipboard.
* **Close repo tab** — remove the clicked tab.
* **Close other tabs** — keep only the clicked tab.
* **Close tabs to the right** — remove everything to its right.

The two "close" actions are greyed out when they would not change
state (only one tab, or clicked tab is already rightmost). The two
"open / copy" actions always emit :attr:`show_folder_requested` /
:attr:`copy_path_requested` carrying the clicked tab's repository
path — :class:`MainWindow` forwards them to the central
:class:`MainViewModel`.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QMouseEvent, QPainter
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QTabBar, QWidget

from src.viewmodels.repo_tabs_viewmodel import RepoTabViewModel


class _CloseTabButton(QWidget):
    """A minimal widget that paints a ``×`` when *hovered*.

    Because it is a plain ``QWidget`` (not ``QPushButton``), no
    style‑sheet or platform‑style padding is added — the tab bar
    sees exactly the size we report.

    The :attr:`clicked_signal` carries no payload (H16): the index
    of the tab to close is resolved at emit-time by the slot via
    :meth:`QTabBar.tabAt` so a stale cached index cannot survive a
    drag-and-drop reorder of the tab bar.
    """

    clicked_signal = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(14, 14)

    # -- hover state ----------------------------------------------------

    def set_hovered(self, hovered: bool) -> None:
        """Show / hide the ``×`` glyph."""
        if self._hovered != hovered:
            self._hovered = hovered
            self.update()

    # -- Qt overrides --------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return QSize(14, 14)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return QSize(14, 14)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            # H16: ship the click position (in *tab-bar* coordinates)
            # so the slot can call ``QTabBar.tabAt(pos)`` and resolve
            # the current tab — the index is no longer cached on the
            # button instance and therefore survives DnD reorder.
            self.clicked_signal.emit(event.pos())

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        if not self._hovered:
            return
        painter = QPainter(self)
        painter.setPen(QColor("#8B8B8B"))
        painter.setFont(QFont("Segoe UI", 11))
        # Shift the glyph upward within the widget so it aligns
        # visually with the tab text (the tab's bottom padding
        # pushes the QTabBar-centering downward).
        r = self.rect().adjusted(0, -2, 0, -2)
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "\u00d7")
        painter.end()


class RepoBarWidget(QWidget):
    """Horizontal bar: ``[Tab1] [Tab2] [+]``.

    Each tab shows the repository folder name and a close glyph that
    appears on tab hover.
    """

    add_requested = Signal()
    show_folder_requested = Signal(str)   # repo root path
    copy_path_requested = Signal(str)     # repo root path

    def __init__(self, view_model: RepoTabViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = view_model
        self._updating_tab_bar: bool = False
        self._hovered_tab: int = -1
        self._close_buttons: dict[int, _CloseTabButton] = {}

        self.setObjectName("repo-bar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tab_bar = QTabBar(self)
        self._tab_bar.setDocumentMode(True)
        self._tab_bar.setTabsClosable(False)
        self._tab_bar.setMovable(True)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setDrawBase(False)
        self._tab_bar.setMouseTracking(True)
        self._tab_bar.installEventFilter(self)
        self._tab_bar.currentChanged.connect(self._on_tab_selected)
        self._tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)
        layout.addWidget(self._tab_bar, stretch=1)

        # Delay hiding so the mouse can move from tab to × without
        # the glyph disappearing.
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(200)
        self._hide_timer.timeout.connect(self._on_hide_timer)

        self._add_btn = QPushButton("+", self)
        self._add_btn.setObjectName("repo-add-btn")
        self._add_btn.setFixedSize(28, 28)
        self._add_btn.setFlat(True)
        self._add_btn.clicked.connect(self._on_add_clicked)
        layout.addWidget(self._add_btn)

        self._vm.tabs_changed.connect(self._rebuild_tabs)
        self._vm.active_tab_changed.connect(self._on_active_tab_changed)

        self._rebuild_tabs(self._vm.tabs)
        self._sync_tab_bar_index(self._vm.active_index)

    # ----- event filter -------------------------------------------------

    def eventFilter(  # noqa: N802
        self,
        obj: QWidget,
        event: QEvent,
    ) -> bool:
        if obj is self._tab_bar:
            if event.type() in (QEvent.Type.HoverMove, QEvent.Type.HoverEnter):
                self._hide_timer.stop()
                tab_idx = self._tab_bar.tabAt(event.pos())
                self._set_hovered_tab(tab_idx)
            elif event.type() == QEvent.Type.HoverLeave:
                self._hide_timer.start()
        elif isinstance(obj, _CloseTabButton):
            if event.type() == QEvent.Type.Enter:
                self._hide_timer.stop()
                # Resolve the hovered tab from the button's screen
                # position in the tab bar (H16 — no cached index).
                local_pos = obj.mapTo(self._tab_bar, QPoint(0, 0))
                self._set_hovered_tab(self._tab_bar.tabAt(local_pos))
            elif event.type() == QEvent.Type.Leave:
                self._hide_timer.start()
        return super().eventFilter(obj, event)

    # ----- hover management ---------------------------------------------

    def _set_hovered_tab(self, index: int) -> None:
        for idx, btn in self._close_buttons.items():
            btn.set_hovered(idx == index)
        self._hovered_tab = index

    def _on_hide_timer(self) -> None:
        self._set_hovered_tab(-1)

    # ----- internals ----------------------------------------------------

    def _rebuild_tabs(self, paths: list[str]) -> None:
        self._close_buttons.clear()
        self._updating_tab_bar = True
        self._tab_bar.blockSignals(True)
        while self._tab_bar.count() > 0:
            self._tab_bar.removeTab(0)
        for path in paths:
            label = _tab_label(path)
            idx = self._tab_bar.addTab(label)
            self._tab_bar.setTabData(idx, path)
            self._tab_bar.setTabToolTip(idx, path)
            self._install_close_button(idx)
        self._tab_bar.blockSignals(False)
        self._updating_tab_bar = False
        self._sync_tab_bar_index(self._vm.active_index)

    def _install_close_button(self, index: int) -> None:
        btn = _CloseTabButton(self._tab_bar)
        # H16: tab index is no longer cached on the button — the slot
        # resolves it via ``tabAt`` on every click.
        btn.clicked_signal.connect(self._on_tab_close_requested)
        btn.installEventFilter(self)
        self._tab_bar.setTabButton(
            index, QTabBar.ButtonPosition.RightSide, btn,
        )
        self._close_buttons[index] = btn

    def _sync_tab_bar_index(self, index: int) -> None:
        if 0 <= index < self._tab_bar.count() and self._tab_bar.currentIndex() != index:
            self._tab_bar.setCurrentIndex(index)

    def _on_tab_selected(self, index: int) -> None:
        if self._updating_tab_bar:
            return
        if 0 <= index < self._tab_bar.count():
            self._vm.set_active_tab(index)

    def _on_tab_close_requested(self, local_pos: QPoint) -> None:
        """Resolve the clicked tab via :meth:`QTabBar.tabAt` (H16).

        The position is delivered in the tab-bar's local coordinate
        system (the button re-broadcast ``mousePressEvent``'s
        ``event.pos()``); we then call ``tabAt`` so the index is
        always the *current* one — a drag-and-drop reorder or an
        external :meth:`removeTab` cannot leave a stale cached
        index behind.

        Falls back to ``-1`` so the VM sees "no such tab" instead of
        a bogus close.
        """
        index = self._tab_bar.tabAt(local_pos)
        if index < 0 or index >= self._tab_bar.count():
            return
        self._vm.remove_tab(index)

    def _on_active_tab_changed(self, index: int) -> None:
        if index < 0 or index >= self._tab_bar.count():
            return
        if self._tab_bar.currentIndex() != index:
            self._sync_tab_bar_index(index)

    def _on_add_clicked(self) -> None:
        self.add_requested.emit()

    # ----- right-click context menu -------------------------------------

    def _on_tab_context_menu(self, pos: QPoint) -> None:
        """Build and show the right-click menu for the tab at *pos*.

        No-op when the right click missed every tab — e.g. landed on
        the area between the rightmost tab and the ``+`` button. A
        ``tab_at`` of ``-1`` would otherwise build a menu that
        operates on an undefined index.
        """
        index = self._tab_bar.tabAt(pos)
        if index < 0 or index >= self._tab_bar.count():
            return
        path = self._tab_bar.tabData(index)
        if not isinstance(path, str) or not path:
            return
        actions = self._build_tab_context_menu_actions(
            index, path, self._tab_bar.count(),
        )
        menu = QMenu(self)
        for action in actions:
            menu.addAction(action)
        menu.exec(self._tab_bar.mapToGlobal(pos))

    def _build_tab_context_menu_actions(
        self,
        index: int,
        path: str,
        tab_count: int,
    ) -> list[QAction]:
        """Return the :class:`QAction` list for a right click on *index*.

        Exposed (single underscore) so tests can inspect the actions
        synchronously the way :meth:`_build_branch_menu_actions` is
        consumed in ``test_graph_widget.py``. Splitting the builder
        from the ``QMenu.exec`` keeps tests free of the
        event-loop-blocking modal.

        Disabled-state rules:

        * **Close other tabs** — only enabled when ``tab_count > 1``.
        * **Close tabs to the right** — only enabled when ``index``
          is not already the rightmost tab (``index < tab_count - 1``).
        * The remaining three actions are always enabled; they
          carry the *path* of the clicked tab, not the active one.

        The lambdas capture ``index`` / ``path`` by default so
        ``checked=True`` from ``QAction.triggered`` cannot leak into
        them — same pattern used in
        :class:`src.ui.widgets.left_panel.LeftPanel`.
        """
        actions: list[QAction] = []

        show = QAction("Show repo folder", self)
        show.triggered.connect(
            lambda checked=False, p=path: self.show_folder_requested.emit(p),
        )
        actions.append(show)

        copy = QAction("Copy repo path", self)
        copy.triggered.connect(
            lambda checked=False, p=path: self.copy_path_requested.emit(p),
        )
        actions.append(copy)

        actions.append(self._make_separator())

        close_self = QAction("Close repo tab", self)
        close_self.triggered.connect(
            lambda checked=False, i=index: self._vm.remove_tab(i),
        )
        actions.append(close_self)

        close_others = QAction("Close other tabs", self)
        close_others.setEnabled(tab_count > 1)
        close_others.triggered.connect(
            lambda checked=False, i=index: self._vm.close_others(i),
        )
        actions.append(close_others)

        close_right = QAction("Close tabs to the right", self)
        close_right.setEnabled(index < tab_count - 1)
        close_right.triggered.connect(
            lambda checked=False, i=index: self._vm.close_to_right(i),
        )
        actions.append(close_right)

        return actions

    @staticmethod
    def _make_separator() -> QAction:
        """Return a disabled separator :class:`QAction`.

        ``QMenu.addSeparator()`` cannot be reused outside a real menu
        instance; building separators ahead of time gives the test
        builders a homogeneous list of actions.
        """
        sep = QAction("", None)
        sep.setSeparator(True)
        return sep


def _tab_label(path: str) -> str:
    return Path(path).resolve().name or path


__all__ = ["RepoBarWidget"]
