"""HTTP contract and response failures, using a local OpenAI-compatible test server."""

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest
from src.utils.ai_client import AIClient, AIError, normalize_base_url
from src.utils.ai_config import AISettings
from src.utils.config import load_config


@pytest.fixture
def api_server():
    state = {"requests": [], "status": 200, "response": {"data": [{"id": "chat-b"}]}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.respond()

        def do_POST(self):  # noqa: N802
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append((self.path, self.headers, json.loads(body) if body else None))
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            if state["status"] == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()
            self.wfile.write(json.dumps(state["response"]).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["settings"] = AISettings(
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        api_key="test-secret",
        model="chat-b",
    )
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_models_request_and_authentication(api_server):
    api_server["response"] = {"data": [{"id": "z"}, {"id": "a"}, {"id": "a"}, {}]}
    assert AIClient(api_server["settings"]).list_models() == ["a", "z"]
    path, headers, payload = api_server["requests"][0]
    assert path == "/v1/models"
    assert headers["Authorization"] == "Bearer test-secret"
    assert payload is None


def test_generation_sends_language_prompt_branch_and_diff(api_server):
    api_server["response"] = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "feat: добавить кнопку",
                            "description": "Добавлена генерация сообщения.",
                        }
                    )
                },
                "finish_reason": "stop",
            }
        ]
    }
    result = AIClient(api_server["settings"]).generate_commit_message("+staged code", "feature/ai")
    assert result.summary == "feat: добавить кнопку"
    assert result.description == "Добавлена генерация сообщения."
    path, _, body = api_server["requests"][0]
    assert path == "/v1/chat/completions"
    assert body["model"] == "chat-b" and body["stream"] is False
    assert "Русский" in body["messages"][0]["content"]
    assert "type(scope)" in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"]) == {
        "branch": "feature/ai",
        "staged_diff": "+staged code",
    }
    assert "test-secret" not in repr(api_server["settings"])


def test_connection_checks_chat_completion_without_sending_a_diff(api_server):
    api_server["response"] = {"choices": [{"message": {"content": "OK"}}]}
    client = AIClient(replace(api_server["settings"], api_key=""))
    assert "successful" in client.test_connection()
    _, headers, body = api_server["requests"][0]
    assert headers.get("Authorization") is None
    assert body["messages"] == [{"role": "user", "content": "Reply with OK."}]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 302])
def test_http_errors_do_not_leak_key_or_source_and_do_not_follow_redirects(api_server, status):
    api_server.update(status=status, response={"error": "test-secret +private source"})
    with pytest.raises(AIError, match=f"HTTP {status}") as error:
        AIClient(api_server["settings"]).list_models()
    assert "test-secret" not in str(error.value)
    assert "private source" not in str(error.value)
    assert len(api_server["requests"]) == 1


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "OK"}, "finish_reason": "length"}]},
    ],
)
def test_invalid_chat_response_is_a_domain_error(api_server, response):
    api_server["response"] = response
    with pytest.raises(AIError):
        AIClient(api_server["settings"]).test_connection()


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        "[]",
        '{"summary": "ok"}',
        '{"summary": "two\\nlines", "description": "body"}',
        '{"summary": "ok", "description": ""}',
    ],
)
def test_invalid_generated_message_is_rejected(monkeypatch, content):
    monkeypatch.setattr(AIClient, "complete", lambda *_: content)
    with pytest.raises(AIError, match="invalid commit message"):
        AIClient(AISettings(base_url="http://localhost/v1")).generate_commit_message("diff", "main")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" https://api.example/ ", "https://api.example/v1"),
        ("https://api.example/v1/chat/completions", "https://api.example/v1"),
        ("https://api.example/api/v1/models/", "https://api.example/api/v1"),
        ("http://localhost:1234/custom", "http://localhost:1234/custom"),
    ],
)
def test_base_url_preserves_provider_prefix(value, expected):
    assert normalize_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "localhost:1234",
        "file:///tmp/key",
        "https://user:pass@host/v1",
        "https://host/v1?api_key=secret",
        "https://host:bad/v1",
        "https://host/#fragment",
    ],
)
def test_bad_urls_rejected(value):
    with pytest.raises(AIError):
        normalize_base_url(value)


def test_timeout_and_bad_json_are_domain_errors(monkeypatch):
    opener = MagicMock()
    monkeypatch.setattr("src.utils.ai_client.build_opener", lambda *_: opener)
    opener.open.side_effect = TimeoutError()
    client = AIClient(AISettings(base_url="http://localhost/v1"))
    with pytest.raises(AIError, match="timed out"):
        client.list_models()
    opener.open.side_effect = None
    opener.open.return_value.__enter__.return_value.read.return_value = b"<html>not JSON</html>"
    with pytest.raises(AIError, match="valid JSON"):
        client.list_models()


def test_ai_config_defaults_are_independent_and_invalid_values_fall_back(tmp_path):
    first = load_config(tmp_path / "missing.json")
    first["ai"]["model"] = "must not leak"
    second = load_config(tmp_path / "missing.json")
    assert second["ai"]["model"] == ""
    settings = AISettings.from_config({"ai": {"timeout_seconds": -1, "model": 12}})
    assert settings.timeout_seconds == 60
    assert settings.model == ""


def test_json_fence_is_accepted_without_requiring_provider_json_mode(monkeypatch):
    content = '```json\n{"summary": "feat: add feature", "description": "Explain it"}\n```'
    monkeypatch.setattr(AIClient, "complete", lambda *_: content)
    result = AIClient(AISettings(base_url="http://localhost/v1")).generate_commit_message(
        "diff", "main"
    )
    assert result.summary == "feat: add feature"


@pytest.mark.parametrize("fenced", [False, True])
def test_minimax_inline_reasoning_is_excluded_from_commit_message(monkeypatch, fenced):
    final = json.dumps(
        {"summary": "feat: приветствие", "description": "Обновлён текст приветствия."}
    )
    if fenced:
        final = f"```json\n{final}\n```"
    content = (
        "<think>\nConsider this discarded draft: "
        '{"summary":"wrong draft","description":"do not use"}\n</think>\n\n' + final
    )
    client = AIClient(AISettings(base_url="http://localhost/v1", model="MiniMax-M3"))
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_: {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        },
    )
    result = client.generate_commit_message("+a staged change", "main")
    assert result.summary == "feat: приветствие"
    assert result.description == "Обновлён текст приветствия."


@pytest.mark.parametrize(
    "content",
    [
        '<think>{"summary":"a draft","description":"never use unfinished reasoning"}',
        '<think>{"summary":"a draft","description":"no final answer"}</think>',
        "<think>first block</think><think>unfinished second block",
    ],
)
def test_reasoning_without_final_answer_is_rejected(monkeypatch, content):
    client = AIClient(AISettings(base_url="http://localhost/v1", model="test"))
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_: {
            "choices": [{"message": {"content": content}}],
        },
    )
    with pytest.raises(AIError, match="final answer"):
        client.generate_commit_message("+a staged change", "main")
    with pytest.raises(AIError, match="final answer"):
        client.test_connection()


def test_multiple_leading_reasoning_blocks_preserve_literal_tags_in_final_json(monkeypatch):
    final = {"summary": "fix: handle <think> tags", "description": "Keep literal </think> text."}
    content = " \n<THINK>discard</THINK>\n<think>also discard</think>\n" + json.dumps(final)
    client = AIClient(AISettings(base_url="http://localhost/v1", model="test"))
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_: {
            "choices": [{"message": {"content": content}}],
        },
    )
    result = client.generate_commit_message("+a staged change", "main")
    assert result.summary == final["summary"]
    assert result.description == final["description"]


def test_separate_reasoning_field_is_never_used_as_final_content(monkeypatch):
    final = {"summary": "fix: use final answer", "description": "Only the content field is used."}
    client = AIClient(AISettings(base_url="http://localhost/v1", model="test"))
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_: {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(final),
                        "reasoning_content": "discarded thoughts",
                        "reasoning_details": [{"text": "discarded thoughts"}],
                    }
                }
            ],
        },
    )
    result = client.generate_commit_message("+a staged change", "main")
    assert result.summary == final["summary"]


@pytest.mark.parametrize(
    ("url", "model", "extensions"),
    [
        (
            "https://api.minimax.io/v1/chat/completions",
            "MiniMax-M3",
            {"reasoning_split": True, "thinking": {"type": "disabled"}},
        ),
        ("https://api.minimax.io/v1", "MiniMax-M2.7", {"reasoning_split": True}),
        ("https://api.openai.com/v1", "other-model", {}),
        ("http://localhost:1234/v1", "MiniMax-M3", {}),
        ("https://api.minimax.io.example/v1", "MiniMax-M3", {}),
    ],
)
def test_minimax_native_controls_are_scoped_to_the_api_host(monkeypatch, url, model, extensions):
    client = AIClient(AISettings(provider="Custom", base_url=url, model=model))
    request = MagicMock(return_value={"choices": [{"message": {"content": "OK"}}]})
    monkeypatch.setattr(client, "_request", request)
    client.test_connection()
    endpoint, body = request.call_args.args
    assert endpoint == "chat/completions"
    assert body == {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "stream": False,
        **extensions,
    }


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [
        ("json\n", ""),
        ("Here is the final version.```json\n", "\n```"),
        ("Explanation.\n\n", ""),
        (
            '<think>Consider literal `<think>...</think>` tags.\n'
            '{"summary":"discarded draft","description":"discarded"}\n'
            '</think>\n\n',
            "",
        ),
    ],
)
def test_complete_final_json_after_preamble_preserves_message_text(monkeypatch, prefix, suffix):
    final = {
        "summary": "fix: preserve the final message",
        "description": (
            'Keep literal <think>...</think>, {braces}, "quotes" and ``` fences.\nNext line.'
        ),
    }
    client = AIClient(AISettings(base_url="http://localhost/v1", model="test"))
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_: {
            "choices": [{"message": {"content": prefix + json.dumps(final) + suffix}}],
        },
    )
    result = client.generate_commit_message("+a staged change", "main")
    assert result.summary == final["summary"]
    assert result.description == final["description"]


@pytest.mark.parametrize(
    "content",
    [
        'json\n{"summary":"valid title","description":"cut off',
        '{"summary":"draft","description":"body"}\nMore unfinished thoughts',
        '```json\n{"summary":"draft","description":"body"}\n```\nMore thoughts',
        '[\n{"summary":"wrong container","description":"body"}\n]',
        '<think>\n{"summary":"unfinished draft","description":"body"}',
    ],
)
def test_incomplete_answers_and_nonfinal_drafts_are_not_repaired(monkeypatch, content):
    client = AIClient(AISettings(base_url="http://localhost/v1", model="test"))
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_: {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        },
    )
    with pytest.raises(AIError):
        client.generate_commit_message("+a staged change", "main")


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ({"description": "body"}, "summary must be a non-empty string"),
        ({"summary": "two\nlines", "description": "body"}, "summary must be one line"),
        ({"summary": "title", "description": ["body"]}, "description must be a non-empty string"),
    ],
)
def test_validation_errors_explain_the_format_problem_without_echoing_content(
    monkeypatch, message, reason
):
    monkeypatch.setattr(AIClient, "complete", lambda *_: json.dumps(message))
    with pytest.raises(AIError, match=reason):
        AIClient(AISettings(base_url="http://localhost/v1")).generate_commit_message("diff", "main")
