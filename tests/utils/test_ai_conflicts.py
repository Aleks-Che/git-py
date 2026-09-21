"""Compact complete conflict payloads, strict answers and protected surrounding text."""
import json

import pytest
from src.core.conflict_resolution import ConflictSnapshot
from src.utils.ai_client import AIClient, AIError
from src.utils.ai_config import AISettings
from src.utils.ai_conflicts import resolve_with_ai


def _snapshot():
    return ConflictSnapshot("f", b"header\nold\nfooter\n", b"header\nleft\nfooter\n",
                            b"header\nright\nfooter\n", "main abc", "feature def")


def test_ai_receives_conflicts_context_labels_and_preserves_surroundings(monkeypatch):
    calls = []

    def complete(client, messages):
        calls.append((client.settings, messages))
        return json.dumps({"resolutions": [{"id": 1, "content": "resolved\n"}]})

    monkeypatch.setattr(AIClient, "complete", complete)
    settings = AISettings(base_url="http://localhost:1234/v1", model="configured",
                          conflict_prompt="shared instruction", conflict_context="keep both")
    assert resolve_with_ai(_snapshot(), settings) == "header\nresolved\nfooter\n"
    assert calls[0][0] == settings
    messages = calls[0][1]
    assert "shared instruction" in messages[0]["content"]
    assert "keep both" in messages[1]["content"]
    payload = json.loads(messages[2]["content"])
    assert payload["target"] == "main abc" and payload["incoming"] == "feature def"
    assert payload["conflicts"][0]["base"] == "old\n"
    assert payload["conflicts"][0]["context_before"] == "header\n"
    assert "-left" in payload["conflicts"][0]["diff"]


@pytest.mark.parametrize("answer", [
    "not json", "[]", '{"content":"wrong schema"}',
    '{"resolutions":[]}',
    '{"resolutions":[{"id":1,"content":1}]}',
    '{"resolutions":[{"id":true,"content":"x"}]}',
    '{"resolutions":[{"id":99,"content":"x"}]}',
    '{"resolutions":[{"id":1,"content":"x"},{"id":1,"content":"y"}]}',
    '{"resolutions":[{"id":1,"content":"<<<<<<< HEAD"}]}',
])
def test_invalid_or_incomplete_answers_are_rejected(monkeypatch, answer):
    monkeypatch.setattr(AIClient, "complete", lambda *args: answer)
    with pytest.raises(AIError):
        resolve_with_ai(_snapshot(), AISettings(base_url="http://localhost"))


def test_empty_resolution_ignores_commit_diff_limit(monkeypatch):
    monkeypatch.setattr(AIClient, "complete",
                        lambda *args: '{"resolutions":[{"id":1,"content":""}]}')
    settings = AISettings(base_url="http://localhost", max_diff_chars=10)
    assert resolve_with_ai(_snapshot(), settings) == "header\nfooter\n"


def test_large_file_small_conflict_is_not_truncated(monkeypatch):
    context = "".join(f"line {i}\n" for i in range(8000))
    snapshot = ConflictSnapshot("f", (context + "old\n").encode(),
                                (context + "left\n").encode(), (context + "right\n").encode())

    def complete(client, messages):
        assert len(messages[2]["content"]) < 2000
        return json.dumps({"resolutions": [{"id": 1, "content": "resolved\n"}]})

    monkeypatch.setattr(AIClient, "complete", complete)
    result = resolve_with_ai(snapshot, AISettings(base_url="http://localhost"))
    assert result == context + "resolved\n"


def test_multiple_conflicts_composed_in_file_order(monkeypatch):
    snapshot = ConflictSnapshot("f", b"a\ngap\nb", b"L\ngap\nM", b"R\ngap\nN")
    answer = {"resolutions": [{"id": 2, "content": "last"}, {"id": 0, "content": "first\n"}]}
    monkeypatch.setattr(AIClient, "complete", lambda *args: json.dumps(answer))
    assert resolve_with_ai(snapshot, AISettings(base_url="http://localhost")) == "first\ngap\nlast"
