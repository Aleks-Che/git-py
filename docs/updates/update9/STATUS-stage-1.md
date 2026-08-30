# STATUS update9 stage-1 — show public key + Copy button in SettingsDialog

## Что сделано

Пользовательский фидбек (Александр Чесноков):

> когда ключ введен надо в settings под путями ключей показать текстовый вид публичного ключа и добавить кнопку иконку для копирования ключа в буфер обмена

Раньше пользователь должен был открывать `.pub` файл вручную, копировать содержимое
и вставлять в GitHub/GitLab/Bitbucket. Теперь всё видно и копируется одним кликом.

## Изменения

### `src/ui/icons.py` — новая иконка `copy`

- Добавлена функция `_draw_copy(p: QPainter)`: два перекрывающихся прямоугольника
  (классический glyph «copy/clipboard») — back rectangle (source page) +
  front rectangle (destination page), обе с открытым верхом для эффекта «страница».
- Зарегистрирована в `_DRAWERS` под ключом `"copy"`.
- Иконка автоматически получает все три режима (normal/disabled/active)
  через существующий `_render` + `toolbar_icon`.

### `src/ui/dialogs/settings_dialog.py` — read-only preview + Copy

1. **Read-only QPlainTextEdit** (`_ssh_pub_view`):
   - Высота 70-110px (multi-line).
   - Mono-space шрифт (`QFont("Monospace")` + `StyleHint.TypeWriter`).
   - Placeholder: *"No public key file found at the configured path."*
2. **Кнопка Copy** (`_copy_btn: QToolButton`):
   - Иконка `toolbar_icon("copy")` + текст "Copy" (side by side).
   - Tooltip: *"Copy public key to clipboard"*.
   - Click → `QApplication.clipboard().setText(self._ssh_pub_view.toPlainText())`.
   - Feedback: `QToolTip.showText("Copied!", 2s)` рядом с кнопкой.
3. **Debounced refresh** (`_refresh_pubkey_timer`):
   - `QTimer`, single-shot, 150ms interval.
   - `textChanged` от `_ssh_priv_edit` и `_ssh_pub_edit` → `timer.start()`.
   - `_refresh_public_key_view()`: читает `.pub` файл (приоритет: явный
     pub path → `priv + ".pub"` → clear).
4. **Размер окна**: с `540x320` до `640x480` чтобы вместить preview.
5. **Initial load**: `_refresh_public_key_view()` вызывается в `_load_from_config()`
   чтобы содержимое появилось сразу при открытии диалога.

## Тесты

### Изменён

- (нет)

### Добавлены в `tests/ui/test_settings_dialog.py`

- `test_settings_dialog_shows_public_key_content` — создаёт `id_test` +
  `id_test.pub` в `tmp_path`, открывает диалог, проверяет что preview
  содержит `"ssh-ed25519"` и `"user@host"`.
- `test_settings_dialog_copy_button_copies_to_clipboard` — кликает `_copy_btn`,
  проверяет что `QApplication.clipboard().text()` равен содержимому `.pub`.
  Патчит `QToolTip.showText` чтобы избежать позиционирования tooltip
  на скрытом виджете.
- `test_settings_dialog_reads_public_key_on_path_change` — устанавливает
  один pub, потом меняет путь на другой, ждёт debounce через
  `qtbot.waitUntil(lambda: "SECOND" in view, timeout=2000)`.
- `test_settings_dialog_shows_placeholder_when_pub_missing` — путь ведёт
  к несуществующему файлу, проверяет что view пустой + placeholder видим.

## Гейты

```
$ ruff check src/ tests/
All checks passed!

$ QT_QPA_PLATFORM=offscreen python -m pytest tests/ui/ -q
575 passed in 45.30s
```

Было 571 в update8 (24 → 25 → 26 в clone_dialog + 5 → 9 в settings_dialog + 540 other ui).
Стало 575 — четыре новых теста для update9.

## Commits

```
3eb7861 stage-1(update9): show public key content + copy button in SettingsDialog
```

## Что НЕ сделано (out of scope для update9)

- Не показываю `Copy` кнопку для **приватного** ключа — это security risk.
  Пользователь может случайно расшарить свой закрытый ключ.
- Не делаю `Ctrl+C` shortcut в preview — пользователь может использовать
  стандартный copy из контекстного меню QPlainTextEdit.
- Не делаю отображение публичного ключа **в `SshKeyDialog`** (где ключ
  генерируется) — там уже есть `_output` QLineEdit с публичным ключом.
- Не делаю drag-and-drop `.pub` файла на preview.

## Регрессии

Прогон `tests/core/`, `tests/viewmodels/`, `tests/utils/` не делал в этой стадии
(изменения изолированы в `src/ui/icons.py` + `src/ui/dialogs/settings_dialog.py`).
