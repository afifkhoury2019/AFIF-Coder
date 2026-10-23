"""Model backends. The agent keeps one conversation in OpenAI chat format; each backend
streams one assistant turn from its source and returns it in that same format.

- LocalBackend:     the built-in AFIF Engine (llama.cpp server)
- OpenAIBackend:    Gemini, DeepSeek and any OpenAI-compatible service
- AnthropicBackend: Claude, through Anthropic's native Messages API
"""
from __future__ import annotations

import base64
import json
import queue
import socket
import threading
import time
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
    free: bool = False      # has a free tier: listed under "Free cloud" and used by Free Auto
    budget: int = 32000     # tokens of instructions + chat history to send per turn
    max_tokens: int = 0     # cap on each reply (0 = the service's default)
    keyless: bool = False   # works without a pasted key (GitHub Models uses the GitHub sign-in)


PROVIDERS = {
    "anthropic": Provider(
        "anthropic", "Claude", "anthropic", "https://api.anthropic.com",
        "https://platform.claude.com/settings/keys",
        "Billed per use by Anthropic (separate from a Claude.ai subscription).",
        ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"], budget=60000),
    "gemini": Provider(
        "gemini", "Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
        "https://aistudio.google.com/apikey",
        "Google AI Studio keys include a free tier with daily limits.",
        ["gemini-3.8-flash"], free=True, budget=24000),
    "deepseek": Provider(
        "deepseek", "DeepSeek", "openai", "https://api.deepseek.com",
        "https://platform.deepseek.com/api_keys",
        "Billed per use by DeepSeek; low prices.",
        ["deepseek-flash", "deepseek-v4-pro"], vision=False, echo_reasoning=True, budget=32000),
    "groq": Provider(
        "groq", "Groq", "openai", "https://api.groq.com/openai/v1",
        "https://console.groq.com/keys",
        "Free tier, very fast. Small per-minute limits, so AFIF Coder sends a short prompt.",
        ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3-32b"],
        vision=False, free=True, budget=5500, max_tokens=2048),
    "cerebras": Provider(
        "cerebras", "Cerebras", "openai", "https://api.cerebras.ai/v1",
        "https://cloud.cerebras.ai",
        "Free or trial credits depending on the account; very fast.",
        ["gpt-oss-120b", "qwen-3-coder-480b"], vision=False, free=True, budget=7000, max_tokens=2048),
    "mistral": Provider(
        "mistral", "Mistral", "openai", "https://api.mistral.ai/v1",
        "https://console.mistral.ai/api-keys",
        "Free 'Experiment' plan (phone check) with rate limits. Devstral and Codestral are built for code.",
        ["devstral-latest", "mistral-medium-latest", "codestral-latest"], vision=False, free=True, budget=20000),
    "openrouter": Provider(
        "openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
        "https://openrouter.ai/keys",
        "Models ending in \":free\" cost nothing (about 50 requests a day without credit).",
        [], vision=False, free=True, budget=16000, max_tokens=4096),
    "github": Provider(
        "github", "GitHub Models", "openai", "https://models.github.ai/inference",
        "",
        "Free with your GitHub sign-in (Connections). Daily limits; about 8,000 tokens per request.",
        ["openai/gpt-4.1", "openai/gpt-4.1-mini", "deepseek/deepseek-v3-0324"],
        vision=False, free=True, budget=6500, max_tokens=3000, keyless=True),
    "custom": Provider(
        "custom", "Other", "openai", "",
        "",
        "Any OpenAI-compatible service: OpenRouter, OpenAI, Groq, Mistral, LM Studio…",
        [], vision=False, custom=True),
}


def github_token() -> str:
    """The token of the GitHub sign-in (Connections), used for GitHub Models."""
    try:
        from . import tooling
        code, out = tooling.run(["gh", "auth", "token"], timeout=15)
        tok = out.strip().splitlines()[-1].strip() if code == 0 and out.strip() else ""
        return tok if tok.startswith(("gho_", "ghp_", "ghu_", "github_pat_")) else ""
    except Exception:
        return ""


def make_backend(provider_id: str, model: str, cfg: dict) -> "Backend":
    p = PROVIDERS[provider_id]
    key = cfg.get("key", "")
    if not key and p.keyless:
        key = github_token()
        if not key:
            raise BackendError("Sign in to GitHub in Connections first; GitHub Models uses that sign-in.")
    if not key and provider_id != "custom":
        raise BackendError(f"Add your {p.label} API key in Models → Cloud first.")
    if p.kind == "anthropic":
        return AnthropicBackend(key, model)
    base = (cfg.get("base_url") or p.base_url).rstrip("/")
    if not base:
        raise BackendError("Set the address of your OpenAI-compatible service in Models → Cloud.")
    b = OpenAIBackend(base, key, model, vision=cfg.get("vision", p.vision), echo_reasoning=p.echo_reasoning,
                      label=p.label)
    b.provider = provider_id
    b.effort_ok = provider_id == "gemini"          # Gemini accepts reasoning_effort
    b.budget_tokens = int(cfg.get("budget") or p.budget)
    if p.max_tokens:
        b.max_tokens = p.max_tokens
    if provider_id == "github":
        b.extra_headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    return b


def list_models(provider_id: str, cfg: dict) -> list[str]:
    """Ask the provider which models this key can use. Raises BackendError with a readable reason."""
    p = PROVIDERS[provider_id]
    key = cfg.get("key", "") or (github_token() if p.keyless else "")
    if p.keyless and not key:
        raise BackendError("Sign in to GitHub in Connections first.")
    try:
        if p.kind == "anthropic":
            r = HTTP.get(f"{p.base_url}/v1/models?limit=100", timeout=20,
                             headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
            _raise_for(r, p.label)
            return [m["id"] for m in r.json().get("data", [])]
        if provider_id == "github":
            r = HTTP.get("https://models.github.ai/catalog/models", timeout=20,
                         headers={"Authorization": f"Bearer {key}", "Accept": "application/vnd.github+json"})
            _raise_for(r, p.label)
            data = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            ids = [m.get("id", "") for m in data if "tool-calling" in (m.get("capabilities") or [])]
            return sorted(i for i in ids if i)
        base = (cfg.get("base_url") or p.base_url).rstrip("/")
        r = HTTP.get(f"{base}/models", timeout=20, headers={"Authorization": f"Bearer {key}"} if key else {})
        _raise_for(r, p.label)
        data = r.json()
        data = data.get("data", []) if isinstance(data, dict) else data
        if provider_id == "openrouter":       # free models that can use tools
            return sorted(m["id"] for m in data if m.get("id", "").endswith(":free")
                          and "tools" in (m.get("supported_parameters") or []))
        ids = [m.get("id", "").removeprefix("models/") for m in data]
        if provider_id == "gemini":     # keep chat models that can use tools
            ids = [i for i in ids if gemini_chat_model(i)]
            return sorted(ids, key=gemini_rank)
        if provider_id in ("groq", "cerebras", "mistral"):
            ids = [i for i in ids if chat_model(i)]
        return sorted(i for i in ids if i)
    except requests.ConnectionError:
        raise BackendError(f"Can't reach {p.label}. Check your internet connection.")
    except requests.Timeout:
        raise BackendError(f"{p.label} didn't answer in time.")


def _unwrap(j):
    """Some services send a JSON list where others send an object; use the first object."""
    while isinstance(j, list):
        j = j[0] if j else {}
    return j if isinstance(j, dict) else {"message": str(j)}


GEMINI_SKIP = ("embedding", "image", "tts", "imagen", "veo", "aqa", "audio", "live", "robotics", "learnlm",
               "computer-use", "computer_use", "banana", "native", "exp-", "gemma", "lyria", "vision",
               "transcribe", "translat", "speech", "music", "deep-research", "diffusion")


NON_CHAT = ("whisper", "tts", "guard", "embed", "moderation", "ocr", "playai", "orpheus", "allam", "voxtral",
            "pixtral", "saba", "transcribe")


def chat_model(model_id: str) -> bool:
    """Speech, safety and embedding models in a provider's list can't chat with tools."""
    return not any(k in model_id.lower() for k in NON_CHAT)


def gemini_chat_model(model_id: str) -> bool:
    """Gemini's list includes special models (computer use, image, speech…) that can't chat with tools."""
    i = model_id.lower()
    return i.startswith("gemini") and not any(s in i for s in GEMINI_SKIP)


def gemini_rank(model_id: str):
    """Newest first; within a version: Flash, Pro, then Lite; stable before preview."""
    import re as _re
    m = _re.search(r"gemini-(\d+(?:\.\d+)?)", model_id)
    ver = float(m.group(1)) if m else 0
    kind = 0 if "flash" in model_id and "lite" not in model_id else (1 if "pro" in model_id else 2)
    return (-ver, kind, "preview" in model_id, model_id)


def _raise_for(r, label: str):
    if r.status_code == 200:
        return
    try:
        j = _unwrap(r.json())                     # Gemini wraps errors in a list: [{"error": {...}}]
        err = j.get("error")
        msg = err.get("message") if isinstance(err, dict) else (err or j.get("message") or r.text)
        if isinstance(err, dict) and err.get("status"):
            msg = f"{msg} [{err['status']}]"
    except ValueError:
        msg = r.text[:300]
    low = str(msg).lower()
    if r.status_code in (401, 403) or "api key" in low or "authentication" in low:
        raise BackendError(f"{label} rejected the API key. Check it in Models → Cloud. ({msg})")
    if r.status_code == 413 or "request too large" in low or "tokens per minute" in low and "requested" in low:
        raise BackendError(f"{label}: the request is too large for its limit. ({msg})")
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
_END = object()


class Stopped(Exception):
    """The user pressed Stop while a request was still connecting."""


def post(url, stopped, **kw):
    """POST on a helper thread, checking for Stop every 0.2 s. A cloud model can take 30 s or more
    before it sends the first byte; Stop must not wait for that (the report showed Stop pressed
    seven times and the app restarted). An abandoned request is closed when it finally answers."""
    box: dict = {}
    done = threading.Event()

    def worker():
        try:
            box["r"] = HTTP.post(url, **kw)
        except Exception as e:
            box["e"] = e
        done.set()
        if box.get("abandoned") and box.get("r") is not None:
            box["r"].close()

    threading.Thread(target=worker, daemon=True).start()
    while not done.wait(0.2):
        if stopped():
            box["abandoned"] = True
            raise Stopped()
    if "e" in box:
        raise box["e"]
    return box["r"]


def retry_wait(resp) -> float:
    """Seconds to wait before retrying a busy (5xx) or briefly rate-limited (429) service, or 0 when
    waiting won't help (a daily quota is used up, or the wait would be long)."""
    import re as _re
    if resp.status_code not in (429, 500, 502, 503, 529):
        return 0
    text = (resp.text or "")[:4000].lower()
    m = _re.search(r'retry(?:delay|[ _-]?after|\s+in)\W{0,4}(\d+(?:\.\d+)?)\s*s', text)
    if m:
        secs = float(m.group(1))
        return secs if secs <= 45 else 0
    if resp.status_code == 429 and any(w in text for w in ("per day", "perday", "daily", "requestsperday")):
        return 0
    try:
        ra = float(resp.headers.get("Retry-After", 0))
    except ValueError:
        ra = 0
    if ra > 45:
        return 0
    return max(ra, 3.0)


def _pump(resp, stopped):
    """Read the stream on a helper thread and hand lines over through a queue, checking for Stop
    every 0.2 s. A Stop therefore ends the reply at once on every OS, even while the socket read
    is still blocked (Windows doesn't wake a blocked read on shutdown)."""
    q: queue.Queue = queue.Queue()

    def reader():
        try:
            for line in _lines(resp):
                q.put(line)
        except Exception as e:          # handed to the consumer
            q.put(e)
        q.put(_END)

    threading.Thread(target=reader, daemon=True).start()
    while True:
        try:
            item = q.get(timeout=0.2)
        except queue.Empty:
            if stopped():
                return
            continue
        if item is _END:
            return
        if isinstance(item, Exception):
            if stopped():
                return
            raise item
        yield item


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


def _short_id(i: str) -> str:
    """Mistral only accepts tool-call ids of 9 letters and digits."""
    import hashlib
    return i if len(i) == 9 and i.isalnum() else hashlib.md5(i.encode()).hexdigest()[:9]


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
    timing: dict = {}           # last request: status, latency, engine timings (for the debug report)
    label = ""
    vision = False
    speed = "balanced"
    on_thinking = None          # callable(str): live reasoning text, so the user sees progress

    def __init__(self):
        self.resp = None

    def _think(self, text: str):
        if self.on_thinking and text:
            self.on_thinking(text)

    on_progress = None          # callable(done, total): prompt-reading progress (local engine)
    on_status = None            # callable(str): short status line ("busy, retrying…", "switched to…")
    budget_tokens = 32000       # how much chat history to send each turn (see Agent._view)

    def close(self):
        try:
            if self.resp is not None:
                self.resp.close()
        except Exception:
            pass

    def abort(self):
        """Stop a reply from ANY thread without blocking, and wake the thread that is reading it.

        Closing the response would wait for the reader. Shutting the socket down wakes the reader on
        Linux; Windows only wakes it when the socket is really closed, which needs the makefile()
        wrapper released too. The engine notices the closed connection and stops working."""
        r = self.resp
        if r is None:
            return
        sock, sio = None, None
        try:
            sock = getattr(getattr(r.raw, "_connection", None), "sock", None)
            fp = getattr(r.raw, "_fp", None)
            sio = getattr(getattr(fp, "fp", None), "raw", None)          # socket.SocketIO
            if sock is None:
                sock = getattr(sio, "_sock", None)
        except Exception:
            pass
        for step in (lambda: sock.shutdown(socket.SHUT_RDWR), lambda: sock.close(), lambda: sio.close()):
            try:
                step()
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
            if pm.get("tool_calls"):
                pm["tool_calls"] = [self._call_for_server(c) for c in pm["tool_calls"]]
            if m["role"] == "tool" and self.provider == "mistral":
                pm["tool_call_id"] = _short_id(pm.get("tool_call_id", ""))
            out.append(pm)
        flush()
        return out

    def _call_for_server(self, c: dict) -> dict:
        """One chat can move between services (Free Auto). Each gets tool calls in the form it accepts:
        Gemini needs a thought signature on every call, Mistral 9-character ids, others no extra fields."""
        out = {"id": c.get("id", ""), "type": "function", "function": c["function"]}
        if self.provider == "gemini":
            extra = c.get("extra_content") or {"google": {"thought_signature": "skip_thought_signature_validator"}}
            out["extra_content"] = extra
        elif self.provider == "mistral":
            out["id"] = _short_id(out["id"])
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
        headers.update(self.extra_headers)
        url = f"{self.base}/chat/completions"
        t0 = time.perf_counter()
        self.timing = {"host": self.base.split("/")[2] if "//" in self.base else self.base, "retries": [],
                       "request_chars": len(json.dumps(body))}
        self.resp = self._send(url, body, headers, stopped, t0)
        if self.resp is None:
            return None
        content, reasoning, calls, usage, tps = [], [], {}, (0, 0), 0.0
        try:
            self.timing["status"] = self.resp.status_code
            if self.resp.status_code != 200:
                self.timing["error_body"] = self.resp.text[:800]
                _raise_for(self.resp, self.label)
            for raw in _pump(self.resp, stopped):     # each event the moment it arrives
                if "first_event_s" not in self.timing:
                    self.timing["first_event_s"] = round(time.perf_counter() - t0, 3)
                if stopped():
                    return None
                if not raw or not raw.startswith(b"data:"):
                    continue
                data = raw[5:].strip()
                if data == b"[DONE]":
                    break
                chunk = _unwrap(json.loads(data))
                if chunk.get("error"):
                    e = chunk["error"]
                    raise BackendError(str(e.get("message", e) if isinstance(e, dict) else e))
                if chunk.get("usage"):
                    u = chunk["usage"]
                    usage = (u.get("prompt_tokens", 0) or 0, u.get("completion_tokens", 0) or 0)
                if chunk.get("timings"):                            # llama-server speed report
                    tm = chunk["timings"]
                    tps = float(tm.get("predicted_per_second") or 0)
                    self.timing.update(engine_prompt_ms=round(tm.get("prompt_ms") or 0), engine_prompt_n=tm.get("prompt_n"),
                                       engine_cache_n=tm.get("cache_n"), engine_gen_ms=round(tm.get("predicted_ms") or 0),
                                       engine_gen_n=tm.get("predicted_n"),
                                       engine_prompt_tps=round(tm.get("prompt_per_second") or 0, 1))
                pp = chunk.get("prompt_progress")
                if pp:                                               # llama-server reading progress
                    self.timing.update(prompt_tokens=int(pp.get("total", 0)), prompt_cached=int(pp.get("cache", 0)),
                                       prompt_read_ms=int(pp.get("time_ms", 0)))
                    if self.on_progress:
                        self.on_progress(int(pp.get("processed", 0)), int(pp.get("total", 0)))
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
        except (requests.exceptions.RequestException, AttributeError, OSError, ValueError) as e:
            if stopped():
                return None
            if isinstance(e, (requests.exceptions.ChunkedEncodingError, requests.ConnectionError, OSError)):
                raise BackendError(f"The connection to {self.label} dropped. Try again.")
            raise
        finally:
            if not stopped():
                self.close()
            self.resp = None
        if stopped():
            return None
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
    provider = ""
    extra_headers: dict = {}
    max_waits = 2               # short waits on "busy" / per-minute limits; Free Auto switches instead

    def _send(self, url, body, headers, stopped, t0):
        """Send the request; drop options a server doesn't know, and wait out short busy spells."""
        waits = 0
        while True:
            try:
                r = post(url, stopped, json=body, headers=headers, stream=True, timeout=(10, 900))
            except Stopped:
                return None
            except requests.ConnectionError:
                raise BackendError(f"Can't reach {self.label}. Check your internet connection.")
            self.timing["headers_s"] = round(time.perf_counter() - t0, 3)
            if r.status_code == 400:
                bad = next((o for o in ("stream_options", "reasoning_effort", "chat_template_kwargs", "return_progress")
                            if o in body and o in r.text), None)
                if bad:                       # a server that doesn't know this option: retry without it
                    r.close()
                    body.pop(bad)
                    self.timing["retries"].append(f"without {bad}")
                    continue
            wait = retry_wait(r) if waits < self.max_waits else 0
            if not wait:
                return r
            waits += 1
            self.timing["retries"].append(f"waited {wait:.0f}s after HTTP {r.status_code}")
            r.close()
            if self.on_status:
                self.on_status(f"{self.label} is busy; trying again in {wait:.0f} s…")
            end = time.time() + wait
            while time.time() < end:
                if stopped():
                    return None
                time.sleep(0.2)

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

    @property
    def budget_tokens(self) -> int:
        """Send only as much chat as this PC reads in about a minute and a half (the engine re-reads
        whatever isn't cached, e.g. a long saved chat after a restart), and never more than fits."""
        from .engine import SPEED
        cap = int(self.context_tokens * 0.6)
        tps = SPEED.get("prompt_tps") or 0
        if not tps:
            return min(cap, 6000)
        return int(min(cap, max(4000, tps * 90 // 2000 * 2000)))     # in steps, so it stays put between turns

    def extra_body(self) -> dict:
        think = self.speed == "thorough"
        # Thinking models (Qwen3, gpt-oss…) answer much sooner with thinking off; others ignore these.
        return {"chat_template_kwargs": {"enable_thinking": think, "reasoning_effort": "medium" if think else "low"},
                "cache_prompt": True, "return_progress": True}

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
            turn = super().stream(*a, **k)
        except requests.ConnectionError:
            raise BackendError("The AFIF Engine isn't running. Pick a model at the top, then try again.")
        except BackendError as e:
            if "Can't reach" in str(e):
                raise BackendError("The AFIF Engine isn't running. Pick a model at the top, then try again.")
            raise
        if (self.timing.get("engine_prompt_n") or 0) >= 200 and self.timing.get("engine_prompt_tps"):
            from .engine import SPEED
            SPEED["prompt_tps"] = float(self.timing["engine_prompt_tps"])
        return turn


# ---------------------------------------------------------------- Free Auto
# When a service hits its limit it rests for a while; Free Auto moves on to the next one.
COOLDOWN: dict[str, float] = {}         # candidate name -> time it can be used again


def _rest_for(err: str) -> float:
    low = err.lower()
    if "out of credit" in low or "rejected the api key" in low or "sign in to github" in low:
        return 6 * 3600
    if "can't use tools" in low:
        return 24 * 3600
    if "per day" in low or "daily" in low or "perday" in low or "requests per day" in low:
        return 3600
    if "rate limit" in low or "quota" in low or "429" in low or "too large" in low or "413" in low:
        return 70
    return 30                           # busy, connection dropped, unknown error


class RouterBackend(Backend):
    """Free Auto: use the free services you've connected one after another. When one is busy, out of
    quota or rejects the request, the same turn goes to the next (like FreeLLMAPI, but built in).
    The agent hands it `payload(budget, lean)`, so each service gets a prompt sized to its limits."""
    label = "Free Auto"
    router = True

    def __init__(self, candidates: list):
        super().__init__()
        self.candidates = candidates    # [(name, factory -> Backend)], best first
        self.current = None             # backend that answered last
        self.current_name = ""
        self.payload = None             # set by the agent: payload(budget, lean) -> (messages, tools)
        self.model = "free-auto"

    @property
    def budget_tokens(self):
        return self.current.budget_tokens if self.current else 6000

    def abort(self):
        if self.current:
            self.current.abort()

    def stream(self, messages, tools, temperature, on_text, stopped) -> Turn | None:
        tried, last = [], ""
        now = time.time()
        order = [c for c in self.candidates if COOLDOWN.get(c[0], 0) <= now] or \
            sorted(self.candidates, key=lambda c: COOLDOWN.get(c[0], 0))[:1]
        for name, factory in order:
            if stopped():
                return None
            try:
                b = factory()
            except BackendError as e:
                COOLDOWN[name] = time.time() + _rest_for(str(e))
                tried.append(f"{name}: {e}")
                continue
            b.speed, b.on_thinking, b.on_progress, b.on_status = self.speed, self.on_thinking, self.on_progress, \
                self.on_status
            b.max_waits = 0 if len(order) > 1 else 2      # don't wait: switch to the next service
            self.current, self.current_name = b, name
            said = []
            msgs, tls = self.payload(b.budget_tokens, b.budget_tokens < 12000 or getattr(b, "local", False))
            try:
                turn = b.stream(msgs, tls, temperature, lambda t: (said.append(t), on_text(t)), stopped)
            except BackendError as e:
                self.timing = dict(b.timing, model=name)
                if said:                                   # part of the answer is already shown
                    raise
                COOLDOWN[name] = time.time() + _rest_for(str(e))
                tried.append(f"{name}: {str(e)[:160]}")
                last = str(e)
                if self.on_status:
                    self.on_status(f"{name} isn't available right now; trying the next free model…")
                from . import diag
                diag.event("free_auto_switch", level="info", model=name, error=str(e)[:600], request=b.timing)
                continue
            self.timing = dict(b.timing, model=name, tried=tried)
            return turn
        raise BackendError("Every free model is busy or out of quota right now. Wait a few minutes, connect "
                           "another free service in Models → Cloud, or pick a model on this PC.\n"
                           + "\n".join(tried[-6:]) if tried else last or "No free service is connected yet.")


# ---------------------------------------------------------------- Anthropic (Claude)
class AnthropicBackend(Backend):
    label = "Claude"
    vision = True
    budget_tokens = 60000       # prompt caching makes re-sent history cheap
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
        t0 = time.perf_counter()
        self.timing = {"host": "api.anthropic.com", "retries": [], "request_chars": len(json.dumps(body))}
        waits = 0
        while True:
            try:
                self.resp = post(self.URL, stopped, json=body, headers=headers, stream=True, timeout=(10, 900))
            except Stopped:
                self.resp = None
                return None
            except requests.ConnectionError:
                raise BackendError("Can't reach Claude. Check your internet connection.")
            self.timing["headers_s"] = round(time.perf_counter() - t0, 3)
            if self.resp.status_code == 400 and "output_config" in body and (
                    "output_config" in self.resp.text or "effort" in self.resp.text):
                self.timing["retries"].append("without effort")
                self.resp.close()                       # older model without effort support
                body.pop("output_config")
                continue
            wait = retry_wait(self.resp) if waits < 2 else 0
            if not wait:
                break
            waits += 1
            self.timing["retries"].append(f"waited {wait:.0f}s after HTTP {self.resp.status_code}")
            self.resp.close()
            if self.on_status:
                self.on_status(f"Claude is busy; trying again in {wait:.0f} s…")
            end = time.time() + wait
            while time.time() < end:
                if stopped():
                    self.resp = None
                    return None
                time.sleep(0.2)
        blocks: dict[int, dict] = {}
        tin = tout = 0
        event = ""
        try:
            self.timing["status"] = self.resp.status_code
            if self.resp.status_code != 200:
                self.timing["error_body"] = self.resp.text[:800]
                _raise_for(self.resp, "Claude")
            for raw in _pump(self.resp, stopped):
                if "first_event_s" not in self.timing:
                    self.timing["first_event_s"] = round(time.perf_counter() - t0, 3)
                if stopped():
                    return None
                if not raw:
                    continue
                if raw.startswith(b"event:"):
                    event = raw[6:].strip().decode()
                    continue
                if not raw.startswith(b"data:"):
                    continue
                d = _unwrap(json.loads(raw[5:].strip()))
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
        except (requests.exceptions.RequestException, AttributeError, OSError, ValueError) as e:
            if stopped():
                return None
            if isinstance(e, (requests.exceptions.ChunkedEncodingError, requests.ConnectionError, OSError)):
                raise BackendError(f"The connection to {self.label} dropped. Try again.")
            raise
        finally:
            if not stopped():
                self.close()
            self.resp = None
        if stopped():
            return None

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
