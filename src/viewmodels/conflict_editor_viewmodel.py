"""Ordered conflict choices and asynchronous AI drafts, independent of widgets."""
from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.core.conflict_resolution import (
    ConflictSnapshot,
    compare_three_way,
    decode_text,
    load_conflict,
)
from src.core.exceptions import GitError
from src.core.repository import RepositoryManager
from src.utils.ai_client import AIError
from src.utils.ai_config import AISettings
from src.utils.ai_conflicts import resolve_with_ai
from src.utils.config import default_config_path, load_config, save_config
from src.utils.latest_worker import LatestWorker

Token = tuple[int, str, int]


@dataclass(frozen=True)
class ConflictRow:
    text: str
    number: int | None = None
    token: Token | None = None
    changed: bool = False
    selected: bool = False
    emphasis: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class ResultLine:
    text: str
    region: int
    token: Token | None = None


class ConflictEditorViewModel(QObject):
    changed = Signal()
    busy_changed = Signal(bool)
    error_occurred = Signal(str)
    ai_finished = Signal()

    def __init__(self, parent=None, config_path: Path | None = None) -> None:
        super().__init__(parent)
        self.config_path = config_path or default_config_path()
        self.snapshot: ConflictSnapshot | None = None
        self.regions = []
        self._source_rows: dict[str, list[ConflictRow]] = {"ours": [], "theirs": []}
        self._result: list[ResultLine] = []
        self._decided: set[int] = set()
        self._empty_choices: set[tuple[int, str]] = set()
        self._binary_result: bytes | None = None
        self._revision = 0
        self.is_busy = False
        self.focus_result_row: int | None = None
        self._ai = LatestWorker(self)
        self._ai.busy_changed.connect(self._set_busy)
        self._ai.finished.connect(self._on_ai_result)
        self._ai.failed.connect(self._on_ai_error)

    def load(self, repo: RepositoryManager, path: str) -> None:
        try:
            self.set_snapshot(load_conflict(repo, path))
        except GitError as exc:
            self.cancel_ai()
            self.snapshot = None
            self.regions = []
            self._source_rows = {"ours": [], "theirs": []}
            self._result = []
            self.changed.emit()
            self.error_occurred.emit(str(exc))

    def set_snapshot(self, snapshot: ConflictSnapshot) -> None:
        self.cancel_ai()
        self.snapshot = snapshot
        self._source_rows = {"ours": [], "theirs": []}
        self._result = []
        self._decided = set()
        self._empty_choices = set()
        self._binary_result = None
        self.focus_result_row = None
        self.regions = []
        if not snapshot.binary:
            try:
                texts = [decode_text(data)[0]
                         for data in (snapshot.base, snapshot.ours, snapshot.theirs)]
                self.regions = compare_three_way(*texts)
            except GitError as exc:
                self.snapshot = None
                self.error_occurred.emit(str(exc))
                self.changed.emit()
                return
            self._build_rows()
            for i, region in enumerate(self.regions):
                if region.automatic is not None:
                    self._result.extend(ResultLine(line, i) for line in region.automatic)
        self._revision += 1
        self.changed.emit()

    @property
    def can_resolve(self) -> bool:
        if self.snapshot is None or self.is_busy:
            return False
        if self.snapshot.binary:
            return self._binary_result is not None
        return all(i in self._decided for i, region in enumerate(self.regions)
                   if region.automatic is None)

    @property
    def remaining(self) -> int:
        return sum(region.automatic is None and i not in self._decided
                   for i, region in enumerate(self.regions))

    def _build_rows(self) -> None:
        numbers = {"ours": 1, "theirs": 1}
        for region_id, region in enumerate(self.regions):
            for tag, a, b, c, d in SequenceMatcher(
                None, region.ours, region.theirs, autojunk=False,
            ).get_opcodes():
                for offset in range(max(b - a, d - c)):
                    pair = [region.ours[a + offset] if a + offset < b else None,
                            region.theirs[c + offset] if c + offset < d else None]
                    emphasis = [[], []]
                    if tag == "replace" and all(line is not None for line in pair):
                        for kind, x, y, u, v in SequenceMatcher(
                            None, pair[0].rstrip("\n"), pair[1].rstrip("\n"), autojunk=False,
                        ).get_opcodes():
                            if kind != "equal":
                                emphasis[0].append((x, y))
                                emphasis[1].append((u, v))
                    for side_index, side in enumerate(("ours", "theirs")):
                        line = pair[side_index]
                        line_index = (a if side == "ours" else c) + offset
                        token = ((region_id, side, line_index)
                                 if line is not None and region.automatic is None else None)
                        self._source_rows[side].append(ConflictRow(
                            (line or "").rstrip("\n"),
                            numbers[side] if line is not None else None,
                            token, tag != "equal" or region.automatic is None,
                            emphasis=tuple(emphasis[side_index]),
                        ))
                        if line is not None:
                            numbers[side] += 1

    def source_rows(self, side: str) -> list[ConflictRow]:
        selected = {line.token for line in self._result if line.token is not None}
        return [replace(row, selected=row.token in selected) for row in self._source_rows[side]]

    def result_rows(self) -> list[ConflictRow]:
        return [ConflictRow(line.text.rstrip("\n"), i + 1, line.token,
                            line.token is not None, line.token is not None)
                for i, line in enumerate(self._result)]

    def side_state(self, side: str) -> int:
        tokens = {row.token for row in self._source_rows[side] if row.token is not None}
        chosen = {line.token for line in self._result if line.token is not None}
        empty = {(i, side) for i, region in enumerate(self.regions)
                 if region.automatic is None and not getattr(region, side)}
        if (tokens or empty) and tokens <= chosen and empty <= self._empty_choices:
            return 2
        if tokens & chosen or empty & self._empty_choices:
            return 1
        return 0

    def add_line(self, token: Token) -> None:
        if self.is_busy or any(line.token == token for line in self._result):
            return
        region_id, side, index = token
        region = self.regions[region_id]
        if region.automatic is not None or side not in ("ours", "theirs"):
            return
        text = getattr(region, side)[index]
        # Append within this conflict, preserving surrounding document order.
        position = next((i for i, line in enumerate(self._result) if line.region > region_id),
                        len(self._result))
        self._result.insert(position, ResultLine(text, region_id, token))
        self.focus_result_row = position
        self._decided.add(region_id)
        self._notify_edit()

    def remove_line(self, token: Token) -> None:
        if self.is_busy:
            return
        self._result = [line for line in self._result if line.token != token]
        self.focus_result_row = None
        self._notify_edit()

    def select_side(self, side: str, selected: bool) -> None:
        if self.is_busy or not self.snapshot:
            return
        if self.snapshot.binary:
            self._binary_result = getattr(self.snapshot, side) if selected else None
            self._notify_edit()
            return
        if selected:
            # Block signals only; all additions still use the same ordering rule.
            previous = self.blockSignals(True)
            for row in self._source_rows[side]:
                if row.token is not None:
                    self.add_line(row.token)
            self.blockSignals(previous)
            self._decided.update(i for i, r in enumerate(self.regions) if r.automatic is None)
            self._empty_choices.update((i, side) for i, r in enumerate(self.regions)
                                       if r.automatic is None and not getattr(r, side))
        else:
            self._result = [line for line in self._result
                            if line.token is None or line.token[1] != side]
            self.focus_result_row = None
            self._empty_choices = {choice for choice in self._empty_choices if choice[1] != side}
        self._notify_edit()

    def result_text(self) -> str:
        return "".join(line.text if line.text.endswith("\n") or i == len(self._result) - 1
                       else line.text + "\n" for i, line in enumerate(self._result))

    def edit_result(self, text: str) -> None:
        if (self.is_busy or self.snapshot is None or self.snapshot.binary
                or text == self.result_text()):
            return
        old_text = self.result_text().splitlines(keepends=True)
        new_text = text.splitlines(keepends=True)
        result = []
        for tag, a, b, c, d in SequenceMatcher(
            None, old_text, new_text, autojunk=False,
        ).get_opcodes():
            if tag == "equal":
                result.extend(replace(line, text=new_text[c + i])
                              for i, line in enumerate(self._result[a:b]))
            else:
                following = (self._result[a].region if a < len(self._result)
                             else max(0, len(self.regions) - 1))
                previous = self._result[a - 1].region if a else 0
                missing = [i for i in range(previous, following + 1)
                           if i < len(self.regions) and self.regions[i].automatic is None
                           and i not in self._decided]
                region = missing[0] if a == b and missing else following
                result.extend(ResultLine(line, region) for line in new_text[c:d])
                self._decided.add(region)
        self._result = result
        self.focus_result_row = None
        self._notify_edit()

    def replace_result(self, text: str) -> None:
        """Replace the whole draft explicitly (AI or an external complete resolution)."""
        if self.is_busy or self.snapshot is None or self.snapshot.binary:
            return
        self.edit_result(text)
        self._decided.update(range(len(self.regions)))
        self.changed.emit()

    def result_bytes(self) -> bytes:
        if self.snapshot is None:
            raise GitError("No conflict loaded.")
        if self.snapshot.binary:
            return self._binary_result or b""
        return self.snapshot.encode(self.result_text())

    def _notify_edit(self) -> None:
        self._revision += 1
        self.changed.emit()

    def ai_settings(self) -> AISettings:
        return AISettings.from_config(load_config(self.config_path))

    def resolve_using_ai(self, prompt: str, context: str) -> None:
        if self.is_busy or self.snapshot is None or self.snapshot.binary:
            return
        try:
            settings = replace(self.ai_settings(), conflict_prompt=prompt, conflict_context=context)
            if not settings.base_url or not settings.model:
                raise AIError("Настройте URL и модель в Settings → AI.")
            if not prompt.strip():
                raise AIError("Введите инструкцию для разрешения конфликтов.")
            config = load_config(self.config_path)
            config.setdefault("ai", {}).update(conflict_prompt=prompt, conflict_context=context)
            save_config(self.config_path, config)
        except (OSError, ValueError, AIError) as exc:
            self.error_occurred.emit(str(exc))
            return
        snapshot = self.snapshot
        self._ai.submit(self._revision, lambda: resolve_with_ai(snapshot, settings))

    def cancel_ai(self) -> None:
        self._ai.invalidate()

    def _set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        self.busy_changed.emit(busy)

    def _on_ai_result(self, revision, result) -> None:
        if revision == self._revision:
            self.replace_result(result)
            self.ai_finished.emit()

    def _on_ai_error(self, revision, error) -> None:
        if revision == self._revision:
            self.error_occurred.emit(str(error) if isinstance(error, AIError | GitError)
                                     else "Не удалось получить решение от AI.")
