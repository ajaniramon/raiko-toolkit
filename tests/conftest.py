"""Shared test setup: repo-on-path, isolated RAIKO_HOME, and a fake streaming
OpenAI client that replays scripted chunks (no network, no keys)."""

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "bench"))

# isolate config + sessions BEFORE engine.config is imported by any test
os.environ.setdefault("RAIKO_HOME", tempfile.mkdtemp(prefix="raiko-tests-"))

import pytest


class NS:
    """Attribute bag whose missing attributes read as None (like pydantic deltas)."""
    def __init__(self, **kw):
        self.__dict__.update(kw)
    def __getattr__(self, k):
        return None


class Chunk:
    def __init__(self, delta=None, usage=None):
        self._usage = usage
        self.choices = [NS(delta=delta)] if delta is not None else []
    def model_dump(self, exclude_none=True):
        d = {"choices": [{"delta": {}}] if self.choices else []}
        if self._usage:
            d["usage"] = self._usage
        return d


def text_delta(t):
    return Chunk(delta=NS(content=t, tool_calls=None, model_extra=None))


def tool_delta(idx, call_id, name, args):
    return Chunk(delta=NS(content=None, model_extra=None,
                          tool_calls=[NS(index=idx, id=call_id,
                                         function=NS(name=name, arguments=args))]))


def usage_chunk(prompt_tokens, completion_tokens):
    return Chunk(usage={"prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens})


def fake_client(script):
    """OpenAI-shaped client replaying `script` (list of chunk lists, one per request)."""
    state = {"i": 0}
    def create(**params):
        chunks = script[state["i"]]
        state["i"] += 1
        class Stream:
            def __iter__(self):
                return iter(chunks)
            def close(self):
                pass
        return Stream()
    return NS(chat=NS(completions=NS(create=create)))


@pytest.fixture
def cfg(tmp_path):
    from engine.config import DEFAULT_CONFIG
    c = json.loads(json.dumps(DEFAULT_CONFIG))
    ws = tmp_path / "workspace"
    ws.mkdir()
    c["permissions"]["workspace"] = str(ws)
    return c
