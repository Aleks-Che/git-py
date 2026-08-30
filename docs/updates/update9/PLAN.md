# PLAN update9 — показать публичный ключ + кнопка Copy в Settings

Дата: 2026-08-30. Источник: пользовательский фидбек.

Контекст: после update8 пользователь генерирует SSH ключ через
`Settings → Generate SSH Key…`. Ключ сохраняется в `~/.ssh/`,
`~/.ssh-py/`, или Tempdir (с информационным сообщением). Но **содержимое
публичного ключа** (то что нужно вставить в GitHub/GitLab/Bitbucket) **не
видно** в Settings — приходится открывать файл вручную, копировать.

Требование:

1. Под полями `SSH Private Key` / `SSH Public Key` в SettingsDialog
   показать **read-only текстовое поле с содержимым `.pub` файла**.
2. Рядом с этим полем — **иконка-кнопка "Copy"** для копирования
   публичного ключа в буфер обмена.
3. Поле должно обновляться **при изменении пути** (пользователь ввёл
   новый путь → мы читаем файл → показываем содержимое).

## Задачи

### 1. Read-only поле публичного ключа в SettingsDialog
- Файл: `src/ui/dialogs/settings_dialog.py`.
- Добавить `QPlainTextEdit` (или `QTextEdit` — read-only) под полями путей.
- Источник содержимого: `ssh_public_key_path + ".pub"` если такой файл есть.
- Если файла нет — поле пустое с placeholder *"No public key file found at <path>"*.
- Read-only (`setReadOnly(True)`), высота 3-4 строки (multi-line).
- Mono-space шрифт (QFontDatabase или просто `setStyleSheet("font-family: monospace")`).

### 2. Кнопка-кнопка с иконкой "Copy"
- Файл: `src/ui/dialogs/settings_dialog.py` + `src/ui/icons.py`.
- Добавить иконку `copy` в `icons.py` (clipboard glyph через QPainter —
  программно, в том же стиле что и существующие).
- Кнопка `QToolButton` или `QPushButton` с этой иконкой и tooltip "Copy to clipboard".
- Click handler → `QApplication.clipboard().setText(self._ssh_pub_view.toPlainText())`.
- Показать короткое подтверждение (QToolTip или status bar message — в
  нашем диалоге нет status bar, поэтому `QToolTip.showText(...)` или
  сменить иконку/текст кнопки на 2 сек).

### 3. Обновление содержимого при изменении путей
- Сигналы `textChanged` от `_ssh_priv_edit` и `_ssh_pub_edit` → обновить
  содержимое read-only поля.
- НО: не блокировать UI на больших файлах. Использовать `QTimer.singleShot(0, ...)`
  для debounce.

### 4. Тесты
- `test_settings_dialog_shows_public_key_content`:
  Создать фикстуру с `priv_key` и `pub_key`, открыть SettingsDialog,
  проверить что read-only поле содержит `.pub` файл.
- `test_settings_dialog_copy_button_copies_to_clipboard`:
  Кликнуть copy-кнопку, проверить `QApplication.clipboard().text()`.
- `test_settings_dialog_reads_public_key_on_path_change`:
  Ввести новый путь → через QTimer → проверить обновление.
- `test_settings_dialog_shows_placeholder_when_pub_missing`:
  Путь указывает на несуществующий `.pub` → placeholder/пусто.

## Этапы
- **Stage A:** `icons.py` — добавить `_draw_copy` и зарегистрировать в `_DRAWERS`.
- **Stage B:** `settings_dialog.py` — read-only поле, кнопка с иконкой, debounce на textChanged.
- **Stage C:** Тесты (4 новых).
- **Stage D:** STATUS-stage-1.md, merge feature/update9 → main, push.

## Технические заметки
- `QPlainTextEdit` лучше `QTextEdit` для plain text (быстрее, легче).
- Иконка должна соответствовать существующему стилю: thin stroke, round caps, монохромная с тремя режимами (normal/disabled/active) — pattern в `_render`.
- Clipboard: `QApplication.clipboard().setText(text)` — synchronous, не требует event loop.
- Для теста clipboard — patch `QApplication.clipboard` через `monkeypatch.setattr`.
- Placeholder в `QPlainTextEdit` — через `setPlaceholderText` (есть в Qt 5.7+, у нас PySide6).

## Тесты / гейты
- pytest: новые + существующие зелёные.
- ruff: `ruff check src/ tests/`.
