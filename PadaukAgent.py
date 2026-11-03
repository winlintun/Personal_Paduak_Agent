#!/usr/bin/env python3
"""
Padauk agent: Ollama LLM (padauk-gemma) + Hirara tool hub.

Usage:
  python3 PadaukAgent.py "your question"
  python3 PadaukAgent.py                (interactive chat)

Tools:
  Hirara hub (localhost:8080): web_search, web_fetch, pdf_read,
    pdf_info, pdf_create, ocr_read, office_read, execute_code
  local_file_read: plain text files (txt/md/csv/...) read from this machine.
  pdf/office/ocr 'path'/'file_path' uploads are resolved here to base64.
"""
import base64
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict

OLLAMA_URL = "http://localhost:11434"
HUB_URL = "http://localhost:8080"
MODEL = "padauk-gemma:latest"   # ctx 8192, num_predict 4096 (patched)
MAX_TURNS = 12

# num_ctx the model was built with (see `ollama show`). Tool schemas, results
# and thinking all share it, so results are trimmed before they get here.
NUM_CTX = 8192
MAX_TOOL_CHARS = 2500           # per tool result
NUM_PREDICT = 2048              # first try; retried lower on a repeat loop
RETRY_PREDICT = 1024
# a stalled turn escalates through these (thinking is part of the budget)
STALL_BUDGETS = (3072, 4096)

# web_search: the hub allows 1-20; 5 is plenty of snippets for a 7.5B model
SEARCH_RESULTS = 5
SEARCH_SNIPPET = 200
SEARCH_DOMAINS = 10

SYSTEM = (
    "You are an agent with tools. Call tools by their EXACT names. "
    "Always use a tool when information or files are needed - never guess. "
    "Final answer in Burmese unless the user asks in English.\n"
    "Search well:\n"
    "- web_search takes a short keyword query (2-5 words, no 'search for'), "
    "in the language the sources are likely written in.\n"
    "- Questions about prices, rates, news, schedules or releases need "
    "web_search; your memory of today is not reliable.\n"
    "- Next turn, answer from the snippets; call web_fetch only when the "
    "snippet is not enough.\n"
    "Tools:\n"
    "- web_search(query) - search the internet\n"
    "- web_fetch(url) - fetch one web page\n"
    "- local_file_read(path) - read a text file (.txt/.md/.csv) on this machine\n"
    "- pdf_read(path) - read a PDF on this machine\n"
    "- office_read(path) - read Word/PPT/Excel (.docx/.pptx/.xlsx)\n"
    "- ocr_read(path) - read scanned PDF/images\n"
    "- pdf_info / pdf_create / execute_code\n"
)

# tiny 7.5B model sometimes invents tool names; map common variants
NAME_FIX = {
    "google_search": "web_search",
    "search": "web_search",
    "search_web": "web_search",
    "web_search_tool": "web_search",
    "read_file": "local_file_read",
    "file_read": "local_file_read",
    "open_file": "local_file_read",
    "read_pdf": "pdf_read",
    "pdf_extract": "pdf_read",
    "read_docx": "office_read",
    "read_document": "office_read",
    "fetch": "web_fetch",
    "browse": "web_fetch",
}

UPLOAD_TOOLS = ("pdf_read", "pdf_info", "ocr_read", "office_read",
                "form_extract")

AVAILABLE = set()      # tool names the model may call
CACHE = OrderedDict()  # (tool, args) -> result, per process
CACHE_MAX = 64


class AgentError(RuntimeError):
    pass


def http_json(url, payload=None, timeout=300):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise AgentError(f"{exc.code} {url}: {detail}") from exc


def http_stream(url, payload, timeout=600):
    """Yield NDJSON events from a streamed Ollama call."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
                line = line.strip()
                if line:
                    yield json.loads(line)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise AgentError(f"{exc.code} {url}: {detail}") from exc


# ------------------------------------------------------------------ tools


def read_text_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    if len(content) > 50000:
        content = content[:50000] + "\n... [truncated]"
    return {"text": content}


def to_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


KEY_MAP = {  # tool -> argument name holding the base64 upload
    "pdf_read": "pdf_base64",
    "pdf_info": "pdf_base64",
    "ocr_read": "file_base64",
    "office_read": "file_base64",
    "form_extract": "file_base64",
}


def normalize_args(name, args):
    """Repair the shapes a small model gets wrong: arguments sent as a JSON
    string, `~` paths, or a URL passed as `path`."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    args = dict(args)
    for key in ("path", "file_path", "pdf_path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            args[key] = os.path.abspath(os.path.expanduser(value.strip()))
    if name in UPLOAD_TOOLS:
        key = KEY_MAP.get(name, "file_base64")
        # the model only ever sees `path` (see load_tool_schemas), but accept
        # any path-like name it may still invent
        path = args.pop("path", None)
        for alt in ("file_path", "pdf_path"):
            if not path and isinstance(args.get(alt), str):
                path = args.pop(alt)
        if path:
            if "://" in path and path.split(":", 1)[0].lower() in ("http", "https"):
                args.setdefault(key.replace("_base64", "_url"), path)
            elif not os.path.isfile(path):
                return None, {"error": f"file not found: {path}"}
            else:
                args[key] = to_b64(path)
    return name, args


def _search_markdown(res):
    lines = []
    for item in res.get("results") or []:
        lines.append(f"- {item.get('title', '')} — {item.get('url', '')}")
        snippet = (item.get("snippet") or "").strip().replace("\n", " ")
        if snippet:
            lines.append(f"  {snippet[:SEARCH_SNIPPET]}")
    return "\n".join(lines)


def _trim_search(res):
    res["results"] = [{"title": r.get("title"), "url": r.get("url"),
                       "snippet": r.get("snippet")}
                      for r in (res.get("results") or [])]
    # the summary survives max_chars truncation even if the list does not
    if not res.get("summary"):
        res["summary"] = _search_markdown(res)
    return res


def _cache_key(name, args):
    query = args.get("query") or args.get("url")
    if name in ("web_search", "web_fetch", "pdf_info") and isinstance(query, str):
        return (name, json.dumps(args, sort_keys=True))
    return None


def run_tool(name, args, use_cache=True):
    name, args = normalize_args(name, args)
    if name is None:
        return {"result": args}
    key = _cache_key(name, args) if use_cache else None
    if key and key in CACHE:
        CACHE.move_to_end(key)
        return {"result": CACHE[key]}

    if name == "web_search":
        try:
            wanted = int(args.get("max_results") or SEARCH_RESULTS)
        except (TypeError, ValueError):
            wanted = SEARCH_RESULTS
        args["max_results"] = max(1, min(wanted, 20))
        for domains in ("allowed_domains", "blocked_domains"):
            value = args.get(domains)
            if not isinstance(value, list):
                args.pop(domains, None)
            elif len(value) > SEARCH_DOMAINS:
                args[domains] = value[:SEARCH_DOMAINS]

    if name == "local_file_read":            # plain text never needs the hub
        result = read_text_file(args.get("path", ""))
    else:
        out = http_json(f"{HUB_URL}/call", {"name": name, "arguments": args})
        result = out.get("result", out)

    result = trim_result(result)
    if key and not (isinstance(result, dict) and result.get("error")):
        CACHE[key] = result
        CACHE.move_to_end(key)
        while len(CACHE) > CACHE_MAX:
            CACHE.popitem(last=False)
    return {"result": result}


def trim_result(result):
    if not isinstance(result, dict):
        return result
    if isinstance(result.get("pdf_base64"), str):
        result["pdf_base64"] = (result["pdf_base64"][:120]
                                + "... (use pdf_read to see text)")
    if "results" in result:
        result = _trim_search(result)
    text = result.get("text")
    if isinstance(text, str) and len(text) > MAX_TOOL_CHARS:
        cut = text.rfind("\n", 0, MAX_TOOL_CHARS)
        result["text"] = (text[:cut if cut > 0 else MAX_TOOL_CHARS]
                          + "\n... [truncated]")
    return result


def load_tool_schemas():
    try:
        tools = http_json(f"{HUB_URL}/schemas", timeout=15)
    except (OSError, ValueError, AgentError):
        tools = []
    tools = tools["tools"] if isinstance(tools, dict) else tools
    tools = [t for t in tools if t.get("name") != "form_extract"]
    for t in tools:
        props = t.setdefault("input_schema", {}).setdefault("properties", {})
        if t["name"] in UPLOAD_TOOLS:
            for k in [k for k in list(props)]:
                if k.endswith(("_path", "_url", "_base64")):
                    del props[k]
            props["path"] = {"type": "string", "description":
                             "Local file path on this machine, to upload."}
        required = [r for r in t["input_schema"].get("required") or []
                    if r in props]
        if "required" in t["input_schema"] or not required:
            t["input_schema"]["required"] = required
        # Ollama's schema check rejects arguments to tools declaring
        # additionalProperties: false, whatever the model leaves unset
        t["input_schema"].pop("additionalProperties", None)
    tools.append({
        "name": "local_file_read",
        "description": "Read a plain-text file (txt, md, csv, log) on this "
                       "machine directly. Prefer this for text files.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    })
    return tools


# -------------------------------------------------------------- chat loop


def _ollama_body(messages, tools, stream, think, num_predict):
    return {"model": MODEL, "think": think, "stream": stream,
            "tools": tools, "messages": messages,
            "keep_alive": "30m",
            "options": {"num_ctx": NUM_CTX, "num_predict": num_predict}}


def _collect(events):
    content, thinking, calls, reason = [], [], [], "stop"
    for ev in events:
        msg = ev.get("message") or {}
        content.append(msg.get("content") or "")
        thinking.append(msg.get("thinking") or "")
        calls.extend(msg.get("tool_calls") or [])
        if ev.get("done"):
            reason = ev.get("done_reason") or "stop"
    return {"message": {"role": "assistant", "content": "".join(content),
                        "thinking": "".join(thinking), "tool_calls": calls},
            "reason": reason}


def _stream(body, think_chunk=16):
    content, thinking, calls, reason = [], [], [], "stop"
    pending = ""
    for ev in http_stream(f"{OLLAMA_URL}/api/chat", body):
        msg = ev.get("message") or {}
        if msg.get("thinking"):
            thinking.append(msg["thinking"])
            pending += msg["thinking"]
            if len(pending) >= think_chunk:
                yield ("thinking", pending)
                pending = ""
        if msg.get("content"):
            content.append(msg["content"])
            yield ("answer", msg["content"])
        calls.extend(msg.get("tool_calls") or [])
        if ev.get("done"):
            reason = ev.get("done_reason") or "stop"
    if pending:
        yield ("thinking", pending)
    yield ("reply", {"role": "assistant", "content": "".join(content),
                     "thinking": "".join(thinking), "tool_calls": calls},
           reason)


def call_model(messages, tools, stream=False, think=True,
               num_predict=NUM_PREDICT):
    """One model round. With stream=True returns an event iterator ending in
    ("reply", message, done_reason); otherwise returns (message, reason)."""
    if stream:
        return _stream(_ollama_body(messages, tools, True, think, num_predict))
    body = _ollama_body(messages, tools, False, think, num_predict)
    return _collect(http_stream(f"{OLLAMA_URL}/api/chat", body))


def _with_retry(attempt, emit, budget=NUM_PREDICT):
    """Ollama answers a runaway repetition with HTTP 500; shrink the budget."""
    try:
        return attempt(num_predict=budget)
    except AgentError as exc:
        if "repeat limit" not in str(exc) or budget <= RETRY_PREDICT:
            raise
        emit("notice", "model looped - retrying with a smaller budget")
        return attempt(num_predict=budget // 2)


def _consume_stream(events, emit=None):
    reply, reason = None, "stop"
    for ev in events:
        if ev[0] == "reply":
            reply, reason = ev[1], ev[2]
        elif emit:
            emit(ev[0], ev[1])
    return reply, reason


def _fix_names(calls, available):
    fixed = []
    for call in calls:
        fn = call.setdefault("function", {})
        name = fn.get("name", "")
        if name not in available:
            fn["name"] = NAME_FIX.get(name, name)
        args = fn.get("arguments")
        fn["arguments"] = args if isinstance(args, dict) else {}
        if fn["name"] in available:
            fixed.append(call)
    return fixed


SEARCH_HINT = re.compile(
    r"(?:query|search(?:es|ed)?(?:\s+the\s+web)?\s+(?:for|about)|look\s+up)"
    r"[^`\n]{0,40}?[`\"']([^`\"']{2,80})[`\"']"
    r"|[`\"']([^`\"']{2,80})[`\"'][^\n]{0,20}?(?:as the query|query should be)",
    re.I)
FILE_HINT = re.compile(r"(/(?:[^\s`'\"]+/)+[^\s`'\"]+\.[A-Za-z0-9]{2,5})")
TOOL_NAMES = ("web_search", "web_fetch", "local_file_read", "pdf_read",
              "pdf_info", "office_read", "ocr_read", "execute_code")


def described_call(thinking):
    """The model sometimes ends its turn describing the call it meant to make
    ('I will use the `web_search` tool with the query "..."') and never emits
    it. Recover that call from its own words instead of asking again."""
    if not thinking:
        return None
    for m in FILE_HINT.finditer(thinking):
        path = m.group(1)
        if os.path.isfile(os.path.expanduser(path)):
            return ("local_file_read", {"path": path})
    if "web_search" not in AVAILABLE:
        return None
    if not any(w in thinking.lower() for w in ("search", "web", "rate", "news")):
        return None
    for m in SEARCH_HINT.finditer(thinking):
        query = (m.group(1) or m.group(2) or "").strip(" .,;:`\"'")
        if len(query) >= 2 and query not in TOOL_NAMES:
            return ("web_search", {"query": query})
    return None


def _run_calls(calls, emit):
    for call in calls:
        fn = call["function"]
        emit("tool_call", fn["name"], fn["arguments"])

    def one(call):
        fn = call["function"]
        started = time.monotonic()
        try:
            res = run_tool(fn["name"], dict(fn["arguments"]))
        except Exception as exc:                    # noqa: BLE001
            res = {"result": {"error": str(exc)}}
        return fn["name"], res, time.monotonic() - started

    # independent calls (e.g. two searches) run at the same time
    if len(calls) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            done = list(pool.map(one, calls))
    else:
        done = [one(c) for c in calls]
    for name, res, _ in done:
        emit("tool_result", name, res)
    return [(name, res) for name, res, _ in done]


def run_turn(messages, tools, stream=False, on_event=None):
    """One user turn. Appends to and returns `messages`.

    on_event(kind, *payload) is called in order:
      ("thinking", text) / ("answer", text)   streamed model output
      ("notice", text)                        retry / fallback notes
      ("tool_call", name, args)               before the tools run
      ("tool_result", name, result)           once each tool has finished
      ("round", n)                            before round n+1
    """
    emit = on_event or (lambda *a: None)

    def round_once(think=True):
        result = _with_retry(
            lambda **kw: call_model(messages, tools, stream=stream,
                                    think=think, **kw), emit)
        if stream:
            return _consume_stream(result, emit)
        return result["message"], result["reason"]

    for round_no in range(MAX_TURNS):
        if round_no:
            emit("round", round_no + 1)
        reply, reason = round_once()
        calls = _fix_names(reply.get("tool_calls") or [], AVAILABLE)
        content = (reply.get("content") or "").strip()

        stalled = 0
        while not calls and not content and stalled < len(STALL_BUDGETS):
            recovered = described_call(reply.get("thinking"))
            if recovered:
                name, args = recovered
                emit("notice", f"ran the {name} call the model described")
                calls = [{"function": {"name": name, "arguments": args}}]
                break
            budget = STALL_BUDGETS[stalled]
            stalled += 1
            emit("notice", f"model stalled - retry with a {budget}-token budget")
            result = _with_retry(
                lambda **kw: call_model(messages, tools, stream=stream,
                                        think=True, **kw), emit, budget=budget)
            if stream:
                reply, reason = _consume_stream(result, None)   # retries are muted
            else:
                reply, reason = result["message"], result["reason"]
            calls = _fix_names(reply.get("tool_calls") or [], AVAILABLE)
            content = (reply.get("content") or "").strip()
        if not calls and not content:
            recovered = described_call(reply.get("thinking"))
            if recovered:
                name, args = recovered
                emit("notice", f"ran the {name} call the model described")
                calls = [{"function": {"name": name, "arguments": args}}]

        if calls:
            messages.append({"role": "assistant", "content": content,
                             "thinking": reply.get("thinking") or "",
                             "tool_calls": calls})
            for name, res in _run_calls(calls, emit):
                messages.append({"role": "tool", "name": name,
                                 "content": json.dumps(
                                     res, ensure_ascii=False)[:MAX_TOOL_CHARS * 2]})
            continue
        if content:
            messages.append({"role": "assistant", "content": reply["content"]})
            return messages
        raise AgentError(f"the model returned no answer ({reason})")

    raise AgentError(f"stopped after {MAX_TURNS} tool rounds")


# ------------------------------------------------------------------- cli


class _CliEvents:
    printed = False

    def __call__(self, kind, *data):
        if kind == "tool_call":
            name, args = data
            preview = args.get("path") or args.get("query") or args.get("url") or ""
            print(f"[tool] {name}  {preview}".rstrip(), file=sys.stderr)
        elif kind == "tool_result":
            name, res = data
            body = res.get("result")
            if isinstance(body, dict) and body.get("error"):
                print(f"[tool] {name} failed: {body['error']}", file=sys.stderr)
        elif kind == "notice":
            print(f"[note] {data[0]}", file=sys.stderr)
        elif kind == "answer":
            print(data[0], end="", flush=True)
            self.printed = True


def main():
    global AVAILABLE
    tools = load_tool_schemas()
    AVAILABLE = {t["name"] for t in tools}
    events = _CliEvents()
    messages = [{"role": "system", "content": SYSTEM}]

    def turn(text):
        events.printed = False
        messages.append({"role": "user", "content": text})
        try:
            run_turn(messages, tools, stream=True, on_event=events)
        except AgentError as exc:
            print(f"\nerror: {exc}", file=sys.stderr)
            messages.pop()                    # do not keep a failed turn
            return
        if events.printed:
            print()
        else:
            print(messages[-1].get("content", "").strip())

    question = " ".join(sys.argv[1:]).strip()
    if question:
        turn(question)
        return
    print("Padauk agent - type a question, Ctrl-D to quit.", file=sys.stderr)
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text:
            turn(text)


if __name__ == "__main__":
    main()
