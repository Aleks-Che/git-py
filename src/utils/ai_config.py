"""Serializable AI settings and editable commit-message prompt presets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

_BASE_PROMPT = (
    "Write a Git commit message based only on the staged diff provided. "
    "Describe the actual changes and their purpose when evident. "
    "Do not invent tests, issue numbers, motivation, or changes absent from the diff. "
    "Use a single-line summary, preferably at most 72 characters, without a trailing period. "
    "Include a useful description explaining the main changes."
)

PROMPT_PRESETS: dict[str, tuple[str, str]] = {
    "conventional": (
        "Conventional Commits",
        _BASE_PROMPT + " Use type(scope): summary; omit scope if unclear. Choose an appropriate "
        "type from feat, fix, docs, refactor, perf, test, build, ci, chore, revert. "
        "Keep type/scope in English. Mark breaking changes only when proven by the diff. "
        "Use concise bullet points in the description.",
    ),
    "plain": (
        "Plain / concise",
        _BASE_PROMPT + " Use a plain imperative summary without a type prefix or emoji. "
        "Keep the description to one or two short sentences.",
    ),
    "detailed": (
        "Detailed / review-friendly",
        _BASE_PROMPT + " Use a plain imperative summary. Organize the description into "
        "bullet points grouped by affected functionality; explain before/after behavior "
        "where the diff supports it. Mention tests only if they appear in the staged diff.",
    ),
    "git_flow": (
        "Git Flow",
        _BASE_PROMPT + " Use Conventional Commits with concise description bullets. "
        "Use the branch name only as a hint: feature/* suggests feat, hotfix/* or bugfix/* "
        "suggests fix, release/* suggests chore(release). The diff takes precedence. "
        "Never invent a release version or claim a merge based only on a branch name. "
        "Keep type/scope in English.",
    ),
}

COMMIT_LANGUAGES = ("Русский", "English", "Deutsch", "Español", "Français", "中文", "日本語")

CONFLICT_PROMPT = (
    "Разреши конфликты слияния Git в переданном файле. Даны общий предок (base), "
    "целевая версия (ours), вливаемая версия (theirs) и diff между ними. "
    "Сохрани независимые изменения обеих сторон и весь неизменённый текст. "
    "Совмести конфликтующие изменения с учётом пользовательского контекста. "
    "Не добавляй несвязанные правки, не оставляй маркеры конфликтов. "
    "Верни решение для каждого конфликтующего участка, включая окончания строк. "
    "Окружающий контекст приводится только для понимания и не входит в заменяемый участок."
)

CONFLICT_PROMPTS: dict[str, str] = {
    "Русский": CONFLICT_PROMPT,
    "English": (
        "Resolve the Git merge conflicts in the supplied file. You are given the common "
        "ancestor (base), target version (ours), incoming version (theirs), and their diff. "
        "Preserve independent changes from both sides and all unchanged text. Combine "
        "conflicting changes according to the user's context. Do not introduce unrelated "
        "changes or leave conflict markers. Return a resolution for each conflicting region, "
        "including line endings. Surrounding context is provided only for understanding "
        "and is not part of the region to replace."
    ),
    "Deutsch": (
        "Löse die Git-Merge-Konflikte in der übergebenen Datei. Du erhältst den gemeinsamen "
        "Vorfahren (base), die Zielversion (ours), die eingehende Version (theirs) und ihren "
        "Diff. Behalte unabhängige Änderungen beider Seiten und den gesamten unveränderten "
        "Text bei. Führe widersprüchliche Änderungen unter Berücksichtigung des vom Benutzer "
        "angegebenen Kontexts zusammen. Füge keine sachfremden Änderungen hinzu und lasse "
        "keine Konfliktmarker stehen. Gib für jeden Konfliktbereich eine Lösung einschließlich "
        "der Zeilenenden zurück. Der umgebende Kontext dient nur dem Verständnis und gehört "
        "nicht zum zu ersetzenden Bereich."
    ),
    "Español": (
        "Resuelve los conflictos de fusión de Git del archivo proporcionado. Se incluyen el "
        "ancestro común (base), la versión de destino (ours), la versión entrante (theirs) y "
        "sus diferencias (diff). Conserva los cambios independientes de ambos lados y todo "
        "el texto que no haya cambiado. Combina los cambios en conflicto según el contexto "
        "del usuario. No introduzcas cambios ajenos ni dejes marcadores de conflicto. Devuelve "
        "una solución para cada bloque en conflicto, incluidos los finales de línea. El "
        "contexto circundante solo sirve para comprender el contenido y no forma parte del "
        "bloque que se debe reemplazar."
    ),
    "Français": (
        "Résous les conflits de fusion Git dans le fichier fourni. Tu disposes de l’ancêtre "
        "commun (base), de la version cible (ours), de la version entrante (theirs) et de leur "
        "diff. Conserve les modifications indépendantes des deux côtés et tout le texte "
        "inchangé. Combine les modifications en conflit en tenant compte du contexte donné "
        "par l’utilisateur. N’ajoute aucune modification sans rapport et ne laisse aucun "
        "marqueur de conflit. Renvoie une résolution pour chaque bloc en conflit, y compris "
        "les fins de ligne. Le contexte environnant sert uniquement à la compréhension et "
        "ne fait pas partie du bloc à remplacer."
    ),
    "中文": (
        "请解决所提供文件中的 Git 合并冲突。输入包含共同祖先版本（base）、目标版本（ours）、"
        "传入版本（theirs）以及它们之间的差异（diff）。保留双方互不冲突的修改和所有未更改的文本。"
        "根据用户提供的上下文整合冲突的修改。不要引入无关更改，也不要留下冲突标记。"
        "为每个冲突区域返回解决后的内容，包括行尾换行符。周围的上下文仅供理解，"
        "不属于需要替换的区域。"
    ),
    "日本語": (
        "提供されたファイルの Git マージ競合を解決してください。共通の祖先（base）、"
        "マージ先のバージョン（ours）、取り込むバージョン（theirs）、および差分（diff）が"
        "与えられます。両側の独立した変更と、変更されていないすべてのテキストを保持してください。"
        "ユーザーの説明に従って競合する変更を統合してください。無関係な変更を加えず、"
        "競合マーカーを残さないでください。各競合領域について、行末の改行を含む解決結果を"
        "返してください。周辺のコンテキストは理解のためだけに提供されており、"
        "置換対象の領域には含まれません。"
    ),
}

PROVIDER_URLS = {
    "Custom": "",
    "OpenAI": "https://api.openai.com/v1",
    "Ollama": "http://localhost:11434/v1",
    "LM Studio": "http://localhost:1234/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
}


@dataclass(frozen=True)
class AISettings:
    provider: str = "Custom"
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    language: str = "Русский"
    preset: str = "conventional"
    commit_prompt: str = PROMPT_PRESETS["conventional"][1]
    timeout_seconds: int = 60
    max_diff_chars: int = 120_000
    conflict_prompt: str = CONFLICT_PROMPT
    conflict_context: str = ""
    conflict_language: str = "Русский"
    include_branch_name: bool = False
    branch_in_summary: bool = True
    branch_in_description: bool = False

    @classmethod
    def from_config(cls, config: dict) -> AISettings:
        raw = config.get("ai", {})
        if not isinstance(raw, dict):
            raw = {}
        defaults = asdict(cls())
        values = {}
        for key, default in defaults.items():
            value = raw.get(key, default)
            if isinstance(default, bool):
                valid = isinstance(value, bool)
            elif isinstance(default, int):
                valid = type(value) is int and value > 0
            else:
                valid = isinstance(value, str)
            values[key] = value if valid else default
        if values["conflict_language"] not in CONFLICT_PROMPTS:
            values["conflict_language"] = "Русский"
        if not isinstance(raw.get("conflict_prompt"), str) or not values["conflict_prompt"].strip():
            values["conflict_prompt"] = CONFLICT_PROMPTS[values["conflict_language"]]
        return cls(**values)

    def to_dict(self) -> dict:
        return asdict(self)
