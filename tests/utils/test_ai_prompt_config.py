"""Migration of older settings, localized defaults and strict Boolean options."""
import pytest
from src.utils.ai_config import CONFLICT_PROMPTS, AISettings
from src.utils.config import load_config, save_config


@pytest.mark.parametrize("language", CONFLICT_PROMPTS)
def test_missing_prompt_defaults_to_selected_language(tmp_path, language):
    path = tmp_path / "config.json"
    save_config(path, {"ai": {"conflict_language": language, "extension": "retained"}})
    config = load_config(path)
    settings = AISettings.from_config(config)
    assert settings.conflict_prompt == CONFLICT_PROMPTS[language]
    assert settings.conflict_language == language
    assert not settings.include_branch_name
    assert settings.branch_in_summary and not settings.branch_in_description
    assert config["ai"]["extension"] == "retained"
    save_config(path, config)
    assert AISettings.from_config(load_config(path)) == settings


@pytest.mark.parametrize("value", [None, "false", "true", 0, 1, [], {}])
def test_invalid_branch_options_use_boolean_defaults(value):
    settings = AISettings.from_config({"ai": {
        "include_branch_name": value, "branch_in_summary": value, "branch_in_description": value,
    }})
    assert settings.include_branch_name is False
    assert settings.branch_in_summary is True
    assert settings.branch_in_description is False


def test_custom_conflict_prompt_survives_old_config_and_bad_language():
    settings = AISettings.from_config({"ai": {"conflict_prompt": "My existing prompt",
                                               "conflict_language": "not supported"}})
    assert settings.conflict_language == "Русский"
    assert settings.conflict_prompt == "My existing prompt"
