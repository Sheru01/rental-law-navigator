"""Model transports: openai (default), anthropic, echo (offline fixtures).

Credentials: the key is read by the vendor SDK from the OPENAI_API_KEY or
ANTHROPIC_API_KEY environment variable and from nowhere else. This module never
reads the value into a variable it keeps, never logs it, and never writes it. Error
text from the SDK is scrubbed through navigator.extract.redact before it is raised.

A transport exposes:
    name, model
    complete(system, messages, doc_id, step) -> str     raw model text (expected JSON)
step is one of "initial", "json_retry", "repair" (the echo transport uses it to pick a fixture).
"""
import json
import os
from pathlib import Path

from navigator.extract.redact import redact

DEFAULT_MODELS = {
    "openai": "gpt-5",
    "anthropic": "claude-sonnet-5-5",
    "echo": "echo-fixture",
}
PIP_LINES = {
    "openai": "python3 -m pip install openai",
    "anthropic": "python3 -m pip install anthropic",
}
KEY_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "echo"


class SetupError(Exception):
    """Missing package or credential. The CLI prints the message and exits 2, no traceback."""


class TransportError(Exception):
    """One call failed. The document is recorded as errored; the run continues."""


class FatalTransportError(TransportError):
    """Credentials rejected. Every later call would fail the same way, so the run stops."""


_FATAL_CLASSES = ("AuthenticationError", "PermissionDeniedError")


def _wrap(exc):
    cls = type(exc).__name__
    msg = redact(str(exc))[:400]
    if cls in _FATAL_CLASSES:
        return FatalTransportError("%s: %s" % (cls, msg))
    return TransportError("%s: %s" % (cls, msg))


class OpenAITransport(object):
    name = "openai"

    def __init__(self, model=None):
        self.model = model or DEFAULT_MODELS["openai"]
        try:
            import openai
        except ImportError:
            raise SetupError(
                "The openai package is not installed. Install it with:\n  %s\nthen run the same command again."
                % PIP_LINES["openai"])
        if not os.environ.get(KEY_VARS["openai"]):
            raise SetupError(
                "OPENAI_API_KEY is not set in this shell. Export it for this terminal session only "
                "(for example: read -rs OPENAI_API_KEY && export OPENAI_API_KEY). "
                "Never write the key to a file.")
        # No api_key argument on purpose: the SDK reads the environment variable itself.
        self._client = openai.OpenAI(max_retries=4, timeout=300)

    def complete(self, system, messages, doc_id, step):
        msgs = [{"role": "system", "content": system}] + list(messages)
        try:
            resp = self._client.chat.completions.create(
                model=self.model, messages=msgs, response_format={"type": "json_object"})
        except Exception as exc:  # noqa: BLE001 - vendor exceptions vary by SDK version
            raise _wrap(exc)
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise TransportError("response truncated at the output token limit (finish_reason=length)")
        return choice.message.content or ""


class AnthropicTransport(object):
    name = "anthropic"

    def __init__(self, model=None):
        self.model = model or DEFAULT_MODELS["anthropic"]
        try:
            import anthropic
        except ImportError:
            raise SetupError(
                "The anthropic package is not installed. Install it with:\n  %s\nthen run the same command again."
                % PIP_LINES["anthropic"])
        if not os.environ.get(KEY_VARS["anthropic"]):
            raise SetupError(
                "ANTHROPIC_API_KEY is not set in this shell. Export it for this terminal session only. "
                "Never write the key to a file.")
        self._client = anthropic.Anthropic(max_retries=4, timeout=300)

    def complete(self, system, messages, doc_id, step):
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=16000, system=system, messages=list(messages))
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc)
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise TransportError("response truncated at the output token limit (stop_reason=max_tokens)")
        return "".join(getattr(b, "text", "") for b in resp.content)


class EchoTransport(object):
    """No network. Returns canned JSON from fixtures/echo/<doc_id>.json.

    Fixture shape: {"response": {...}, "repair_response": {...}}.
    A document without a fixture gets an empty-rules response, so under this transport
    "came back empty" is true by construction and proves nothing about a real model.
    """
    name = "echo"

    def __init__(self, model=None, fixtures_dir=None):
        self.model = model or DEFAULT_MODELS["echo"]
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else FIXTURE_DIR

    def complete(self, system, messages, doc_id, step):
        path = self.fixtures_dir / ("%s.json" % doc_id)
        fx = None
        if path.is_file():
            with open(path, encoding="utf-8") as fh:
                fx = json.load(fh)
        if step == "repair":
            body = (fx or {}).get("repair_response", {"repairs": []})
        elif fx is not None:
            body = fx["response"]
        else:
            body = {"document": {"source_kind": "other", "states_legal_requirement": False,
                                 "notes": "echo transport: no fixture for this document"},
                    "rules": []}
        return json.dumps(body, ensure_ascii=False)


def make_transport(name, model=None, echo_fixtures=None):
    if name == "openai":
        return OpenAITransport(model)
    if name == "anthropic":
        return AnthropicTransport(model)
    if name == "echo":
        return EchoTransport(model, echo_fixtures)
    raise SetupError("unknown transport %r" % name)
