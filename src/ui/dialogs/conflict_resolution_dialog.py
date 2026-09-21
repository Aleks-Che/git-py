"""Two aligned source columns above an editable, ordered resolution draft."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.core.conflict_resolution import is_binary_blob as _is_binary_blob  # noqa: F401
from src.core.exceptions import GitError
from src.core.repository import RepositoryManager
from src.ui.dialogs.ai_conflict_dialog import AIConflictDialog
from src.ui.widgets.conflict_text_widget import ConflictTextWidget
from src.utils.config import load_config, load_splitter_sizes, load_window_size, save_config
from src.viewmodels.conflict_editor_viewmodel import ConflictEditorViewModel


class ConflictResolver:
    """Compatibility interface for external conflict resolvers."""

    def resolve(self, base: str, ours: str, theirs: str) -> str:
        raise NotImplementedError


class ConflictResolutionDialog(QDialog):
    resolved = Signal(str)
    resolved_bytes = Signal(bytes)

    def __init__(self, repo: RepositoryManager | None = None, path: str | None = None,
                 parent: QWidget | None = None, *, config_path: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Разрешение конфликта")
        self.resize(1180, 820)
        self.viewmodel = ConflictEditorViewModel(self, config_path)
        self.save_result: Callable[[bytes], bool] | None = None
        self._layout_config_path = config_path
        self._rendering = False
        self._build_ui()
        self._restore_layout()
        self.viewmodel.changed.connect(self._render)
        self.viewmodel.busy_changed.connect(self._on_busy)
        self.viewmodel.error_occurred.connect(self._show_error)
        self.viewmodel.ai_finished.connect(self._on_ai_finished)
        self.finished.connect(self.viewmodel.cancel_ai)
        self.finished.connect(self._save_layout)
        if repo is not None and path is not None:
            self.set_conflict(repo, path)
        else:
            self._render()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._path_label = QLabel("(no file)")
        self._path_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._path_label)
        hint = QLabel("+ добавляет строку, − возвращает её наверх. Галочки добавляют всю сторону. "
                      "Порядок выбора сохраняется внутри каждого конфликта.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._sources = QSplitter(Qt.Orientation.Horizontal)
        self.ours_view = ConflictTextWidget("ours")
        self.theirs_view = ConflictTextWidget("theirs")
        self.ours_check = QCheckBox("Целевая версия")
        self.theirs_check = QCheckBox("Вливаемая версия")
        self._side_labels = {}
        for side, view, checkbox in (("ours", self.ours_view, self.ours_check),
                                     ("theirs", self.theirs_view, self.theirs_check)):
            container = QWidget()
            column = QVBoxLayout(container)
            column.setContentsMargins(0, 0, 0, 0)
            header = QHBoxLayout()
            header.addWidget(checkbox)
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setMinimumHeight(44)
            header.addWidget(label, 1)
            self._side_labels[side] = label
            column.addLayout(header)
            column.addWidget(view)
            self._sources.addWidget(container)
            checkbox.clicked.connect(lambda checked, side=side:
                                     self.viewmodel.select_side(side, checked))
            view.row_action_requested.connect(self.viewmodel.add_line)
        self.ours_view.verticalScrollBar().valueChanged.connect(
            self.theirs_view.verticalScrollBar().setValue)
        self.theirs_view.verticalScrollBar().valueChanged.connect(
            self.ours_view.verticalScrollBar().setValue)
        self.ours_view.horizontalScrollBar().valueChanged.connect(
            self.theirs_view.horizontalScrollBar().setValue)
        self.theirs_view.horizontalScrollBar().valueChanged.connect(
            self.ours_view.horizontalScrollBar().setValue)
        self._splitter.addWidget(self._sources)
        result = QWidget()
        result_layout = QVBoxLayout(result)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.addWidget(QLabel("Результат — можно редактировать вручную"))
        self._result_view = ConflictTextWidget("result")
        self._result_view.row_action_requested.connect(self.viewmodel.remove_line)
        self._result_view.textChanged.connect(self._on_text_changed)
        result_layout.addWidget(self._result_view)
        self._splitter.addWidget(result)
        self._splitter.setSizes([400, 300])
        layout.addWidget(self._splitter, 1)
        row = QHBoxLayout()
        self._accept_ours_btn = QPushButton("Выбрать целевую версию")
        self._accept_theirs_btn = QPushButton("Выбрать вливаемую версию")
        self._accept_ours_btn.clicked.connect(self.accept_ours_bytes)
        self._accept_theirs_btn.clicked.connect(self.accept_theirs_bytes)
        row.addWidget(self._accept_ours_btn)
        row.addWidget(self._accept_theirs_btn)
        self._ai_btn = QPushButton("Решить с помощью AI…")
        self._ai_btn.clicked.connect(self._open_ai)
        row.addWidget(self._ai_btn)
        self._cancel_ai_btn = QPushButton("Отменить AI")
        self._cancel_ai_btn.clicked.connect(self.viewmodel.cancel_ai)
        self._cancel_ai_btn.hide()
        row.addWidget(self._cancel_ai_btn)
        row.addStretch()
        self._status = QLabel()
        row.addWidget(self._status)
        layout.addLayout(row)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setMaximumHeight(5)
        self._progress.hide()
        layout.addWidget(self._progress)
        self._message = QLabel()
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setWordWrap(True)
        self._message.hide()
        layout.addWidget(self._message)
        self._button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                            | QDialogButtonBox.StandardButton.Cancel)
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить решение")
        self._button_box.accepted.connect(self._on_mark_resolved)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

    def set_conflict(self, repo: RepositoryManager, path: str) -> None:
        self._message.hide()
        self.viewmodel.load(repo, path)
        self._scroll_to_conflict()
        QTimer.singleShot(0, self._scroll_to_conflict)

    def _scroll_to_conflict(self) -> None:
        for view, side in ((self.ours_view, "ours"), (self.theirs_view, "theirs")):
            rows = self.viewmodel.source_rows(side)
            first = next((i for i, row in enumerate(rows) if row.token is not None), 0)
            view.setTextCursor(QTextCursor(view.document().findBlockByNumber(first)))
            view.centerCursor()
        count = 0
        for region in self.viewmodel.regions:
            if region.automatic is None:
                break
            count += len(region.automatic)
        block = self._result_view.document().findBlockByNumber(count)
        if block.isValid():
            self._result_view.setTextCursor(QTextCursor(block))
            self._result_view.centerCursor()

    def _render(self) -> None:
        self._rendering = True
        vm = self.viewmodel
        snapshot = vm.snapshot
        binary = self.is_binary()
        self._path_label.setText(snapshot.path if snapshot else "(no file)")
        for side, view, checkbox in (("ours", self.ours_view, self.ours_check),
                                     ("theirs", self.theirs_view, self.theirs_check)):
            title = "Вливаемая" if side == "theirs" else "Целевая"
            label = getattr(snapshot, side + "_label") if snapshot else title
            checkbox.setText(title)
            checkbox.setToolTip(label)
            self._side_labels[side].setText(label)
            checkbox.setCheckState(Qt.CheckState(vm.side_state(side)))
            checkbox.setEnabled(not binary and not vm.is_busy and snapshot is not None)
            rows = vm.source_rows(side)
            view.set_rows(rows, "Бинарный файл — выберите одну из версий целиком." if binary
                          else "\n".join(row.text for row in rows))
        self._result_view.set_rows(vm.result_rows(), vm.result_text())
        if vm.focus_result_row is not None:
            block = self._result_view.document().findBlockByNumber(vm.focus_result_row)
            self._result_view.setTextCursor(QTextCursor(block))
            self._result_view.ensureCursorVisible()
        self._result_view.setReadOnly(binary or vm.is_busy or snapshot is None)
        self._accept_ours_btn.setVisible(binary)
        self._accept_theirs_btn.setVisible(binary)
        self._ai_btn.setEnabled(snapshot is not None and not binary and not vm.is_busy)
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(vm.can_resolve)
        self._status.setText("AI решает конфликты…" if vm.is_busy else
                             ("Версия выбрана" if vm.can_resolve else "Выберите версию файла")
                             if binary else
                             f"Осталось участков: {vm.remaining}")
        self._rendering = False

    def result_text(self) -> str:
        return self.viewmodel.result_text()

    def set_result_text(self, text: str) -> None:
        self.viewmodel.replace_result(text)

    def result_bytes(self) -> bytes:
        return self.viewmodel.result_bytes()

    def is_binary(self) -> bool:
        return bool(self.viewmodel.snapshot and self.viewmodel.snapshot.binary)

    def accept_ours_bytes(self) -> None:
        self.viewmodel.select_side("ours", True)

    def accept_theirs_bytes(self) -> None:
        self.viewmodel.select_side("theirs", True)

    def _on_text_changed(self) -> None:
        if not self._rendering:
            self.viewmodel.edit_result(self._result_view.toPlainText())

    def _open_ai(self) -> None:
        dialog = AIConflictDialog(self.viewmodel.ai_settings(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._message.hide()
            self.viewmodel.resolve_using_ai(dialog.prompt_edit.toPlainText(),
                                             dialog.context_edit.toPlainText())

    def _on_busy(self, busy: bool) -> None:
        self._progress.setVisible(busy)
        self._cancel_ai_btn.setVisible(busy)
        self._render()

    def _show_error(self, message: str) -> None:
        self._message.setText(message)
        self._message.show()

    def _on_ai_finished(self) -> None:
        self._show_error("AI подготовил результат. Проверьте его и нажмите «Сохранить решение».")
        self._scroll_to_conflict()

    def _restore_layout(self) -> None:
        if self._layout_config_path is None:
            return
        config = load_config(self._layout_config_path)
        layout = config.get("conflict_editor_layout", {})
        if not isinstance(layout, dict):
            return
        self.resize(*load_window_size({"window_size": layout.get("window_size", [1180, 820])}))
        sizes = load_splitter_sizes(layout)
        for name, splitter in (("sources", self._sources), ("result", self._splitter)):
            if len(sizes.get(name, [])) == 2 and all(sizes[name]):
                splitter.setSizes(sizes[name])

    def _save_layout(self) -> None:
        if self._layout_config_path is None:
            return
        config = load_config(self._layout_config_path)
        config["conflict_editor_layout"] = {
            "window_size": [self.width(), self.height()],
            "splitter_sizes": {"sources": self._sources.sizes(), "result": self._splitter.sizes()},
        }
        try:
            save_config(self._layout_config_path, config)
        except OSError:
            pass  # A layout preference must not prevent closing a resolved file.

    def _on_mark_resolved(self) -> None:
        if not self.viewmodel.can_resolve:
            return
        try:
            data = self.result_bytes()
        except GitError as exc:
            self._show_error(str(exc))
            return
        if self.save_result is not None and not self.save_result(data):
            self._show_error("Не удалось сохранить решение. Черновик оставлен открытым.")
            return
        if self.is_binary():
            self.resolved_bytes.emit(data)
        else:
            self.resolved.emit(self.result_text())
        self.accept()


__all__ = ["ConflictResolutionDialog", "ConflictResolver"]
