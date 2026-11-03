# Personal Padauk Agent — Quick Start

Local AI agent: **Padauk model (Ollama)** + **Hirara tools (Docker)**.
Everything runs on your own machine — documents are never uploaded anywhere.

Folder: `~/Desktop/personal_paduak_agent/`

```text
personal_paduak_agent/
├── Hirara/                  tool toolkit project (runs in Docker)
├── PadaukAgent.py           the agent script (Ollama ↔ tools)
├── Padauk-Agent-README.md   this guide
└── docs-test.md             sample test document
```

## Start the tools (one time or after reboot)

```bash
cd ~/Desktop/personal_paduak_agent/Hirara
sg docker -c "docker compose -f docker-compose.slim.yml up -d"
```

Check: `curl localhost:8080/health` → `"status": "ok"`

## Use the agent

```bash
# read a document (txt / md / pdf / docx / pptx / xlsx / scanned pdf)
python3 ~/Desktop/personal_paduak_agent/PadaukAgent.py "Read ~/Documents/report.pdf and summarize in Burmese"

# search the internet (answer streams to the terminal)
python3 ~/Desktop/personal_paduak_agent/PadaukAgent.py "Search the web: today's currency rate USD to MMK"

# interactive terminal chat
python3 ~/Desktop/personal_paduak_agent/PadaukAgent.py

# graphical chat
python3 ~/Desktop/personal_paduak_agent/PadaukChat.py
```

In the chat window: **Enter** sends, **Shift+Enter** starts a new line,
**📎 Attach** adds a document (removable chips above the input), **🔎 Web
search** prefills a search. Each answer shows a gray, clickable
"💭 thinking" block above it; tool activity (search query, result count)
appears in the transcript as it happens.

## Files here

| Item | What it is |
|---|---|
| `Hirara/` | The tool toolkit project (cloned from GitHub, runs in Docker) |
| `Hirara/docker-compose.slim.yml` | Start/stop the tools |
| `PadaukAgent.py` | The agent script (Ollama ↔ tools) |
| `docs-test.md` | Sample test document |

## Notes

- Model used: `padauk-gemma:latest` (patched with num_predict 4096 — fix for the empty-answer bug).
- Tool calling needs `think: true` (handled inside `PadaukAgent.py`).
- The model sometimes ends a turn saying "I will use web_search…" without
  emitting the call. `run_turn` recovers that described call, else retries
  with a larger thinking budget, so a turn never goes silently blank.
- Search stays within `num_ctx 8192`: results are capped at 5 with 200-char
  snippets, repeated searches are cached, and independent tool calls run in
  parallel.
- Missing images (rate-limited by GHCR): retry `docker pull ghcr.io/lucasdmarshall/hirara-fs:latest`
  and `ghcr.io/lucasdmarshall/hirara-browser:latest` later for file/browser tools.
