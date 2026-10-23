"""Model backends. The agent keeps one conversation in OpenAI chat format; each backend
streams one assistant turn from its source and returns it in that same format.

- LocalBackend:     the built-in AFIF Engine (llama.cpp server)
- OpenAIBackend:    Gemini, DeepSeek and any OpenAI-compatible service
- AnthropicBackend: Claude, through Anthropic's native Messages API
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

import requests


class BackendError(Exception):
    pass


# One connection pool for the whole app: no new TLS handshake on every model turn.
HTTP = requests.Session()
HTTP.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8))

# Speed setting -> how hard models think before answering
SPEEDS = ("fast", "balanced", "thorough")
CLAUDE_EFFORT = {"fast": "low", "balanced": "medium", "thorough": "high"}
OPENAI_EFFORT = {"fast": "low", "balanced": "medium", "thorough": "high"}


@dataclass
class Turn:
    """Result of one streamed assistant turn."""
    msg: dict
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_per_s: float = 0.0


# ---------------------------------------------------------------- providers
@dataclass
class Provider:
    id: str
    label: str              # shown in the model list
    kind: str               # "anthropic" | "openai"
    base_url: str
    key_url: str            # where to get a key
    note: str               # one line about cost
    defaults: list[str] = field(default_factory=list)
    vision: bool = True
    echo_reasoning: bool = False
    custom: bool = False


PROVIDERS = {
    "anthropic": Provider(
        "anthropic", "Claude", "anthropic", "https://api.anthropic.com",
        "https://platform.claude.com/settings/keys",
        "Billed per use by Anthropic (separate from a Claude.ai subscription).",
        ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"]),
    "gemini": Provider(
        "gemini", "Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
        "https://aistudio.google.com/apikey",
        "Google AI Studio keys include a free tier with daily limits.",
        ["gemini-3.8-flash"]),
    "deepseek": Provider(
        "deepseek", "DeepSeek", "openai", "https://api.deepseek.com",
        "https://platform.deepseek.com/api_keys",
        "Billed per use by DeepSeek; low prices.",
        ["deepseek-flash", "deepseek-v4-pro"], vision=False, echo_reasoning=True),
    "custom": Provider(
        "custom", "Other", "openai", "",
        "",
        "Any OpenAI-compatible service: OpenRouter, OpenAI, Groq, Mistral, LM Studio…",
        [], vision=False, custom=True),
}


def make_backend(provider_id: str, model: str, cfg: dict) -> "Backend":
    p = PROVIDERS[provider_id]
    key = cfg.get("key", "")
    if not key and provider_id != "custom":
        raise BackendError(f"Add your {p.label} API key in Models → Cloud first.")
    if p.kind == "anthropic":
        return AnthropicBackend(key, model)
    base = (cfg.get("base_url") or p.base_url).rstrip("/")
    if not base:
        raise BackendError("Set the address of your OpenAI-compatible service in Models → Cloud.")
    b = OpenAIBackend(base, key, model, vision=cfg.get("vision", p.vision), echo_reasoning=p.echo_reasoning,
                      label=p.label)
    b.effort_ok = provider_id == "gemini"          # Gemini accepts reasoning_effort
    return b


def list_models(provider_id: str, cfg: dict) -> list[str]:
    """Ask the provider which models this key can use. Raises BackendError with a readable reason."""
    p = PROVIDERS[provider_id]
    key = cfg.get("key", "")
    try:
        if p.kind == "anthropic":
            r = HTTP.get(f"{p.base_url}/v1/models?limit=100", timeout=20,
                             headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
            _raise_for(r, p.label)
            return [m["id"] for m in r.json().get("data", [])]
        base = (cfg.get("base_url") or p.base_url).rstrip("/")
        r = HTTP.get(f"{base}/models", timeout=20, headers={"Authorization": f"Bearer {key}"} if key else {})
        _raise_for(r, p.label)
        ids = [m.get("id", "").removeprefix("models/") for m in r.json().get("data", [])]
        if provider_id == "gemini":     # keep chat models only
            skip = ("embedding", "image", "tts", "imagen", "veo", "aqa", "audio", "live", "robotics", "learnlm")
            ids = [i for i in ids if i.startswith("gemini") and not any(s in i for s in skip)]
        return sorted(i for i in ids if i)
    except requests.ConnectionError:
        raise BackendError(f"Can't reach {p.label}. Check your internet connection.")
    except requests.Timeout:
        raise BackendError(f"{p.label} didn't answer in time.")


def _raise_for(r, label: str):
    if r.status_code == 200:
        return
    try:
        j = r.json()
        err = j.get("error")
        msg = err.get("message") if isinstance(err, dict) else (err or j.get("message") or r.text)
        if isinstance(j, list) and j and isinstance(j[0], dict):        # Gemini sometimes wraps in a list
            msg = j[0].get("error", {}).get("message", msg)
    except ValueError:
        msg = r.text[:300]
    low = str(msg).lower()
    if r.status_code in (401, 403) or "api key" in low or "authentication" in low:
        raise BackendError(f"{label} rejected the API key. Check it in Models → Cloud. ({msg})")
    if r.status_code == 429:
        raise BackendError(f"{label} rate limit or quota reached. Wait a little, or check your plan. ({msg})")
    if r.status_code == 402 or "credit balance" in low or "insufficient" in low and "balance" in low:
        raise BackendError(f"Your {label} account is out of credit. ({msg})")
    if r.status_code in (500, 502, 503, 529) or "overloaded" in low:
        raise BackendError(f"{label} is busy or having problems right now. Try again shortly. ({msg})")
    if "context" in low and ("exceed" in low or "too long" in low or "maximum" in low):
        raise BackendError("The conversation no longer fits in the model's memory. Start a new chat"
                           " or raise the context length in Settings.")
    if "tool" in low and "support" in low:
        raise BackendError("This model can't use tools, so it can't read or edit files. Pick another model.")
    raise BackendError(f"{label} returned an error ({r.status_code}): {msg}")


# ---------------------------------------------------------------- helpers
def _lines(resp):
    """Stream lines the moment the server sends them. (requests' iter_lines waits until a whole
    512-byte buffer fills, which makes replies arrive in delayed bursts.)"""
    if "chunked" in resp.headers.get("Transfer-Encoding", "").lower():
        yield from resp.iter_lines(chunk_size=None)          # one read per chunk as it arrives
        return
    fp = getattr(resp.raw, "_fp", None)
    if fp is None or not hasattr(fp, "read1") or resp.headers.get("Content-Encoding"):
        yield from resp.iter_lines(chunk_size=1)
        return
    buf = b""
    while True:
        data = fp.read1(65536)                               # whatever has arrived, without waiting for more
        if not data:
            break
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.rstrip(b"\r")
    if buf:
        yield buf


def _public(m: dict) -> dict:
    """Drop our private bookkeeping keys before sending a message to a server."""
    return {k: v for k, v in m.items() if not k.startswith("_")}


def text_only(content) -> str:
    """Flatten multi-part content to text for backends that can't see images."""
    if isinstance(content, str):
        return content
    out = []
    for p in content or []:
        if p.get("type") == "text":
            out.append(p["text"])
        elif p.get("type") == "image_url":
            out.append(f"[Image {p.get('_name', '')} not shown: this model can't see images.]")
    return "\n\n".join(out)


class Backend:
    label = ""
    vision = False
    speed = "balanced"
    on_thinking = None          # callable(str): live reasoning text, so the user sees progress

    def __init__(self):
        self.resp = None

    def _think(self, text: str):
        if self.on_thinking and text:
            self.on_thinking(text)

    def close(self):
        try:
            if self.resp is not None:
                self.resp.close()
        except Exception:
            pass

    def stream(self, messages, tools, temperature, on_text, stopped) -> Turn | None:
        raise NotImplementedError


# ---------------------------------------------------------------- OpenAI-compatible
class OpenAIBackend(Backend):
    label = "Cloud"

    def __init__(self, base_url: str, key: str, model: str, vision=True, echo_reasoning=False,
                 temperature_ok=True, label="Cloud"):
        super().__init__()
        self.base, self.key, self.model = base_url, key, model
        self.vision, self.echo_reasoning, self.temperature_ok, self.label = vision, echo_reasoning, temperature_ok, label

    def _prepare(self, messages):
        out, pending = [], []

        def flush():
            if pending:
                parts = [{"type": "text", "text": "Images returned by the tools above:"}]
                parts += [{"type": "image_url", "image_url": {"url": u}} for u in pending]
                out.append({"role": "user", "content": parts})
                pending.clear()

        for m in messages:
            if m["role"] != "tool":
                flush()
            pm = _public(m)
            if m["role"] == "tool" and m.get("_images"):
                if self.vision:
                    pending.extend(m["_images"])
                else:
                    pm["content"] = (pm["content"] or "") + "\n[This model can't see images; the picture was not shown.]"
            if not self.vision and isinstance(pm.get("content"), list):
                pm["content"] = text_only(pm["content"])
            elif isinstance(pm.get("content"), list):
                pm["content"] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in pm["content"]]
            if self.echo_reasoning and m.get("_reasoning") and m["role"] == "assistant":
                pm["reasoning_content"] = m["_reasoning"]
            out.append(pm)
        flush()
        return out

    def stream(self, messages, tools, temperature, on_text, stopped) -> Turn | None:
        body = {"model": self.model, "messages": self._prepare(messages), "tools": tools, "stream": True,
                "stream_options": {"include_usage": True}}
        if getattr(self, "max_tokens", 0):
            body["max_tokens"] = self.max_tokens
        if self.temperature_ok and temperature is not None:
            body["temperature"] = temperature
        body.update(self.extra_body())
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        url = f"{self.base}/chat/completions"
        self.resp = HTTP.post(url, json=body, headers=headers, stream=True, timeout=(10, 900))
        content, reasoning, calls, usage, tps = [], [], {}, (0, 0), 0.0
        try:
            for optional in ("stream_options", "reasoning_effort", "chat_template_kwargs"):
                if self.resp.status_code == 400 and optional in body and optional in self.resp.text:
                    self.resp.close()        # a server that doesn't know this option: retry without it
                    body.pop(optional)
                    self.resp = HTTP.post(url, json=body, headers=headers, stream=True, timeout=(10, 900))
            if self.resp.status_code != 200:
                _raise_for(self.resp, self.label)
            for raw in _lines(self.resp):     # each event the moment it arrives
                if stopped():
                    return None
                if not raw or not raw.startswith(b"data:"):
                    continue
                data = raw[5:].strip()
                if data == b"[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    e = chunk["error"]
                    raise BackendError(str(e.get("message", e) if isinstance(e, dict) else e))
                if chunk.get("usage"):
                    u = chunk["usage"]
                    usage = (u.get("prompt_tokens", 0) or 0, u.get("completion_tokens", 0) or 0)
                if chunk.get("timings"):                            # llama-server speed report
                    tps = float(chunk["timings"].get("predicted_per_second") or 0)
                for ch in chunk.get("choices") or []:
                    d = ch.get("delta") or {}
                    r = d.get("reasoning_content") or d.get("reasoning")
                    if r:
                        reasoning.append(r)
                        self._think(r)
                    if d.get("content"):
                        content.append(d["content"])
                        on_text(d["content"])
                    for tc in d.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", len(calls)),
                                                {"id": "", "name": "", "arguments": "", "extra": {}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        f = tc.get("function") or {}
                        if f.get("name"):
                            slot["name"] += f["name"]
                        if f.get("arguments"):
                            a = f["arguments"]
                            slot["arguments"] += a if isinstance(a, str) else json.dumps(a)
                        for k, v in tc.items():             # e.g. Gemini's thought signatures
                            if k not in ("index", "id", "type", "function"):
                                slot["extra"][k] = v
        except (requests.exceptions.ChunkedEncodingError, AttributeError):
            if stopped():
                return None
            raise
        finally:
            self.close()
            self.resp = None
        msg = {"role": "assistant", "content": "".join(content)}
        if reasoning:
            msg["_reasoning"] = "".join(reasoning)
        if calls:
            msg["tool_calls"] = []
            for i, c in sorted(calls.items()):
                call = {"id": c["id"], "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                call.update(c["extra"])
                msg["tool_calls"].append(call)
        return Turn(msg, *usage, tokens_per_s=tps)

    local = False
    effort_ok = False

    def extra_body(self) -> dict:
        if self.effort_ok:
            return {"reasoning_effort": OPENAI_EFFORT.get(self.speed, "medium")}
        return {}


class LocalBackend(OpenAIBackend):
    def __init__(self, engine_url: str, context_tokens: int = 16384):
        super().__init__(f"{engine_url}/v1", "", "afif-local", vision=False, label="AFIF Engine")
        self.context_tokens = context_tokens
        self.max_tokens = min(4096, context_tokens // 4)      # stop runaway replies filling the memory
        self.local = True

    def extra_body(self) -> dict:
        think = self.speed == "thorough"
        # Thinking models (Qwen3, gpt-oss…) answer much sooner with thinking off; others ignore these.
        return {"chat_template_kwargs": {"enable_thinking": think, "reasoning_effort": "medium" if think else "low"},
                "cache_prompt": True}

    def warm_up(self, messages, tools):
        """Pre-read the instructions and tool list so the first real reply starts quickly."""
        body = {"model": self.model, "messages": self._prepare(messages), "tools": tools, "max_tokens": 1,
                "stream": False, **self.extra_body()}
        try:
            HTTP.post(f"{self.base}/chat/completions", json=body, timeout=(5, 600))
        except requests.RequestException:
            pass

    def stream(self, *a, **k):
        try:
            return super().stream(*a, **k)
        except requests.ConnectionError:
            raise BackendError("The AFIF Engine isn't running. Pick a model at the top, then try again.")


# ---------------------------------------------------------------- Anthropic (Claude)
class AnthropicBackend(Backend):
    label = "Claude"
    vision = True
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, key: str, model: str):
        super().__init__()
        self.key, self.model = key, model

    @staticmethod
    def _tools(tools):
        return [{"name": t["function"]["name"], "description": t["function"]["description"],
                 "input_schema": t["function"]["parameters"]} for t in tools]

    @staticmethod
    def _user_blocks(content) -> list:
        if isinstance(content, str):
            return [{"type": "text", "text": content or "(empty)"}]
        blocks = []
        for p in content:
            if p.get("type") == "text":
                blocks.append({"type": "text", "text": p["text"]})
            elif p.get("type") == "image_url":
                url = p["image_url"]["url"]
                head, _, data = url.partition(",")
                media = head.split(":")[1].split(";")[0] if ":" in head else "image/png"
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
        return blocks or [{"type": "text", "text": "(empty)"}]

    def convert(self, messages):
        system, out = "", []

        def push(role, blocks):
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)          # merge consecutive same-role turns
            else:
                out.append({"role": role, "content": list(blocks)})

        for m in messages:
            r = m["role"]
            if r == "system":
                system += (m["content"] if isinstance(m["content"], str) else text_only(m["content"])) + "\n"
            elif r == "user":
                push("user", self._user_blocks(m["content"]))
            elif r == "tool":
                content = m["content"] or "(no output)"
                if m.get("_images"):
                    content = [{"type": "text", "text": content}] + [
                        self._user_blocks([{"type": "image_url", "image_url": {"url": u}}])[0] for u in m["_images"]]
                push("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": content}])
            elif r == "assistant":
                if m.get("_anthropic"):
                    push("assistant", m["_anthropic"])
                    continue
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m.get("tool_calls") or []:
                    try:
                        args = json.loads(c["function"]["arguments"] or "{}")
                    except ValueError:
                        args = {}
                    blocks.append({"type": "tool_use", "id": c["id"], "name": c["function"]["name"], "input": args})
                if blocks:
                    push("assistant", blocks)
        return system.strip(), out

    def stream(self, messages, tools, temperature, on_text, stopped) -> Turn | None:
        system, msgs = self.convert(messages)
        tools_a = self._tools(tools)
        # Prompt caching: tools + system + conversation so far are re-read at a fraction of the price.
        if tools_a:
            tools_a[-1] = dict(tools_a[-1], cache_control={"type": "ephemeral"})
        if msgs and msgs[-1]["content"]:
            msgs[-1] = dict(msgs[-1], content=list(msgs[-1]["content"]))
            last = msgs[-1]["content"][-1]
            if last.get("type") not in ("thinking", "redacted_thinking"):
                msgs[-1]["content"][-1] = dict(last, cache_control={"type": "ephemeral"})
        body = {"model": self.model, "max_tokens": 32000,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": msgs, "tools": tools_a, "stream": True,
                "output_config": {"effort": CLAUDE_EFFORT.get(self.speed, "medium")}}
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            self.resp = HTTP.post(self.URL, json=body, headers=headers, stream=True, timeout=(10, 900))
            if self.resp.status_code == 400 and ("output_config" in self.resp.text or "effort" in self.resp.text):
                self.resp.close()                       # older model without effort support
                body.pop("output_config")
                self.resp = HTTP.post(self.URL, json=body, headers=headers, stream=True, timeout=(10, 900))
        except requests.ConnectionError:
            raise BackendError("Can't reach Claude. Check your internet connection.")
        blocks: dict[int, dict] = {}
        tin = tout = 0
        event = ""
        try:
            if self.resp.status_code != 200:
                _raise_for(self.resp, "Claude")
            for raw in _lines(self.resp):
                if stopped():
                    return None
                if not raw:
                    continue
                if raw.startswith(b"event:"):
                    event = raw[6:].strip().decode()
                    continue
                if not raw.startswith(b"data:"):
                    continue
                d = json.loads(raw[5:].strip())
                t = d.get("type", event)
                if t == "error":
                    e = d.get("error", {})
                    raise BackendError(f"Claude: {e.get('message', e)}")
                if t == "message_start":
                    u = d["message"].get("usage", {})
                    tin = (u.get("input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0) \
                        + (u.get("cache_creation_input_tokens", 0) or 0)
                elif t == "content_block_start":
                    b = dict(d["content_block"])
                    if b["type"] == "tool_use":
                        b["_json"] = ""
                    blocks[d["index"]] = b
                elif t == "content_block_delta":
                    b, dl = blocks[d["index"]], d["delta"]
                    k = dl.get("type")
                    if k == "text_delta":
                        b["text"] = b.get("text", "") + dl["text"]
                        on_text(dl["text"])
                    elif k == "input_json_delta":
                        b["_json"] += dl.get("partial_json", "")
                    elif k == "thinking_delta":
                        b["thinking"] = b.get("thinking", "") + dl.get("thinking", "")
                        self._think(dl.get("thinking", ""))
                    elif k == "signature_delta":
                        b["signature"] = b.get("signature", "") + dl.get("signature", "")
                elif t == "message_delta":
                    tout = d.get("usage", {}).get("output_tokens", tout) or tout
                elif t == "message_stop":
                    break
        except (requests.exceptions.ChunkedEncodingError, AttributeError):
            if stopped():
                return None
            raise
        finally:
            self.close()
            self.resp = None

        raw_blocks, text, calls = [], [], []
        for i in sorted(blocks):
            b = blocks[i]
            if b["type"] == "tool_use":
                try:
                    b["input"] = json.loads(b.pop("_json") or "{}")
                except ValueError:
                    b["input"] = {}
                calls.append({"id": b["id"], "type": "function",
                              "function": {"name": b["name"], "arguments": json.dumps(b["input"])}})
            elif b["type"] == "text":
                text.append(b.get("text", ""))
            raw_blocks.append(b)
        msg = {"role": "assistant", "content": "".join(text), "_anthropic": raw_blocks}
        if calls:
            msg["tool_calls"] = calls
        return Turn(msg, tin, tout)


# ---------------------------------------------------------------- key storage
def protect(secret: str) -> str:
    """Encrypt an API key for this Windows user (DPAPI). Plain base64 elsewhere."""
    if not secret:
        return ""
    data = secret.encode("utf-8")
    try:
        import sys
        if sys.platform == "win32":
            return "dpapi:" + base64.b64encode(_dpapi(data, True)).decode()
    except Exception:
        pass
    return "b64:" + base64.b64encode(data).decode()


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    try:
        kind, _, val = stored.partition(":")
        raw = base64.b64decode(val)
        if kind == "dpapi":
            return _dpapi(raw, False).decode("utf-8")
        return raw.decode("utf-8")
    except Exception:
        return ""


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if encrypt:
        ok = fn(ctypes.byref(inp), "AFIF Coder", None, None, None, 0, ctypes.byref(out))
    else:
        ok = fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
