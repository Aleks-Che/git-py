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

    @classmethod
    def from_config(cls, config: dict) -> AISettings:
        raw = config.get("ai", {})
        if not isinstance(raw, dict):
            raw = {}
        defaults = asdict(cls())
        values = {}
        for key, default in defaults.items():
            value = raw.get(key, default)
            if isinstance(default, int):
                valid = type(value) is int and value > 0
            else:
                valid = isinstance(value, str)
            values[key] = value if valid else default
        return cls(**values)

    def to_dict(self) -> dict:
        return asdict(self)
