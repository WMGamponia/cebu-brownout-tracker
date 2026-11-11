"""LLM entity extractors (spec 4.4): request shapes, schema check, retries, cache, fallback."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from outages import db
from outages.areas import llm
from outages.areas.entities import Entity
from outages.areas.segment import segment
from outages.config import HAIKU_MODEL, OLLAMA_URL, QWEN_MODEL

TEXT = "Portion of Cebu City (Labogon) including portion of Sitio Chicos, Proper and Tabay."
KEY = "sk-ant-test-0123456789-secret"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def ollama_reply(obj) -> httpx.Response:
    content = obj if isinstance(obj, str) else json.dumps(obj)
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


GOOD = {
    "entities": [
        {"kind": "sitio_purok", "name": "Sitio Chicos"},
        {"kind": "sitio_purok", "name": "Proper"},
        {"kind": "sitio_purok", "name": "Tabay"},
    ]
}


def ollama(conn, handler) -> tuple[llm.OllamaExtractor, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req, len(seen))

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return llm.OllamaExtractor(conn, client=client), seen


def test_render_input_has_labels_and_no_barangays():
    seg = segment("Portion of Mandaue City\nBarangays: Jubay, Cotcot\nStreets: Jasmin Street")
    text = llm.render_input(seg)
    assert "Streets: Jasmin Street" in text
    assert "Jubay" not in text and "Mandaue" not in text


def test_ollama_request_shape(conn):
    ex, seen = ollama(conn, lambda req, n: ollama_reply(GOOD))
    ex.extract(segment(TEXT))
    req = seen[0]
    assert str(req.url) == f"{OLLAMA_URL}/api/chat"
    body = json.loads(req.content)
    assert body["model"] == QWEN_MODEL
    assert body["think"] is False
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert body["format"] == llm.ENTITY_SCHEMA
    assert "Sitio Chicos" in body["messages"][-1]["content"]


def test_valid_response_parsed_and_normalized(conn):
    ex, _ = ollama(conn, lambda req, n: ollama_reply(GOOD))
    got = ex.extract(segment(TEXT))
    assert got == [
        Entity("sitio_purok", "Chicos"),
        Entity("sitio_purok", "Proper"),
        Entity("sitio_purok", "Tabay"),
    ]
    assert ex.last_extractor == "qwen"


def test_validate_rejects_bad_shapes():
    for bad in (
        [],
        {},
        {"entities": "x"},
        {"entities": [{"kind": "road", "name": "A"}]},
        {"entities": [{"kind": "street", "name": "  "}]},
        {"entities": [{"kind": "street"}]},
    ):
        with pytest.raises(llm.SchemaError):
            llm.validate(bad)
    assert llm.validate({"entities": []}) == []


def test_invalid_then_valid_retries(conn):
    ex, seen = ollama(conn, lambda req, n: ollama_reply("not json" if n == 1 else GOOD))
    got = ex.extract(segment(TEXT))
    assert len(seen) == 2
    assert len(got) == 3 and ex.last_extractor == "qwen"


def test_invalid_twice_falls_back_to_rules(conn):
    ex, seen = ollama(conn, lambda req, n: ollama_reply({"entities": [{"kind": "x", "name": "y"}]}))
    got = ex.extract(segment(TEXT))
    assert len(seen) == 2
    assert ex.last_extractor == "rules_fallback"
    assert Entity("sitio_purok", "Chicos") in got
    assert conn.execute("SELECT count(*) FROM llm_cache").fetchone()[0] == 0


def test_timeout_retries_once_then_raises(conn):
    def handler(req, n):
        raise httpx.ReadTimeout("slow", request=req)

    ex, seen = ollama(conn, handler)
    with pytest.raises(llm.LLMUnavailable):
        ex.extract(segment(TEXT))
    assert len(seen) == 2


def test_unreachable_raises_llm_unavailable(conn):
    def handler(req, n):
        raise httpx.ConnectError("refused", request=req)

    ex, _ = ollama(conn, handler)
    with pytest.raises(llm.LLMUnavailable) as e:
        ex.extract(segment(TEXT))
    assert OLLAMA_URL in str(e.value)
    assert "ENTITY_EXTRACTOR=rules" in str(e.value)


def test_cache_hit_makes_no_call(conn):
    ex, seen = ollama(conn, lambda req, n: ollama_reply(GOOD))
    first = ex.extract(segment(TEXT))
    row = conn.execute("SELECT model, prompt_version, seconds FROM llm_cache").fetchone()
    assert tuple(row)[:2] == (QWEN_MODEL, llm.PROMPT_VERSION) and row[2] >= 0
    again, seen2 = ollama(conn, lambda req, n: pytest.fail("called the model on a cache hit"))
    assert again.extract(segment(TEXT)) == first
    assert seen2 == [] and len(seen) == 1
    assert again.last_extractor == "qwen"


def test_prompt_version_changes_key(monkeypatch):
    text = llm.render_input(segment(TEXT))
    k1 = llm.cache_key(QWEN_MODEL, text)
    monkeypatch.setattr(llm, "PROMPT_VERSION", "999")
    assert llm.cache_key(QWEN_MODEL, text) != k1
    assert llm.cache_key(HAIKU_MODEL, text) != k1


def test_empty_input_makes_no_call(conn):
    ex, seen = ollama(conn, lambda req, n: pytest.fail("called the model with nothing to read"))
    assert ex.extract(segment("Portion of Cebu City (Labogon).")) == []
    assert seen == []


def anthropic_reply(obj) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "content": [{"type": "tool_use", "id": "t1", "name": llm.TOOL_NAME, "input": obj}],
            "stop_reason": "tool_use",
        },
    )


def test_anthropic_request_shape(conn):
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        return anthropic_reply(GOOD)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ex = llm.AnthropicExtractor(conn, KEY, client=client)
    assert ex.name == "haiku"
    got = ex.extract(segment(TEXT))
    assert Entity("sitio_purok", "Tabay") in got
    req = seen[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == KEY
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body["model"] == HAIKU_MODEL
    assert body["tools"][0]["input_schema"] == llm.ENTITY_SCHEMA
    assert body["tool_choice"] == {"type": "tool", "name": llm.TOOL_NAME}


def test_anthropic_skipped_without_key(conn, monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    assert llm.anthropic_from_env(conn) is None


def test_anthropic_key_from_dotenv(conn, monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f'# local\nANTHROPIC_API_KEY="{KEY}"\n', encoding="utf-8")
    ex = llm.anthropic_from_env(conn)
    assert ex is not None and ex.api_key == KEY


def test_key_never_logged(conn, caplog):
    caplog.set_level(logging.DEBUG)

    def handler(req):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ex = llm.AnthropicExtractor(conn, KEY, client=client)
    with pytest.raises(llm.LLMUnavailable) as e:
        ex.extract(segment(TEXT))
    assert KEY not in caplog.text
    assert KEY not in str(e.value)
    assert KEY not in repr(ex)


@pytest.mark.llm
def test_qwen_real_call(conn):
    row = json.loads(
        next(open("evals/areas/dev.jsonl", encoding="utf-8"))  # noqa: SIM115
    )
    ex = llm.OllamaExtractor(conn)
    got = ex.extract(segment(row["areas_raw"]))
    assert ex.last_extractor == "qwen"
    assert all(
        e.kind in ("street", "sitio_purok", "subdivision", "establishment", "other") for e in got
    )
