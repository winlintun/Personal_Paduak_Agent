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

## One-time setup: download the model (GGUF)

The model file is **not** stored in this GitHub repository — it is ~8 GB and
belongs on Hugging Face. After cloning this project, download and import it
into Ollama:

**Model: <https://huggingface.co/WYNN747/ai4burmese-padauk-gguf>**

```bash
# 1) download the GGUF (~7 GB) and the Modelfile from the Hugging Face repo
curl -L -o padauk-gemma-q8_0.gguf \
  "https://huggingface.co/WYNN747/ai4burmese-padauk-gguf/resolve/main/padauk-gemma-q8_0.gguf"
curl -L -o Modelfile \
  "https://huggingface.co/WYNN747/ai4burmese-padauk-gguf/resolve/main/Modelfile"

# 2) patch the Modelfile — the published one has num_predict 512, which makes
#    thinking exhaust the output budget and return empty answers
sed -i 's/num_predict 512/num_predict 4096/' Modelfile

# 3) import into Ollama under the name the agent expects
ollama create padauk-gemma -f Modelfile

# optional: delete the ~7 GB GGUF after importing (Ollama keeps its own copy)
rm padauk-gemma-q8_0.gguf
```

Verify: `ollama list` must show `padauk-gemma:latest`.

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

# graphical chat (primary, Qt)
python3 ~/Desktop/personal_paduak_agent/PadaukChatQt.py

# graphical chat (Tk fallback; Myanmar text may not shape correctly)
python3 ~/Desktop/personal_paduak_agent/PadaukChat.py
```

In the chat window: **Enter** sends, **Shift+Enter** starts a new line,
**📎 Attach** adds a document (removable chips above the input), **🔎 Web
search** prefills a search. Answers are rendered as Markdown (headings, lists,
tables, code blocks), each answer shows a gray, clickable "💭 thinking" block
above it, and tool activity (search query, result count) appears in the
transcript as it happens. **■ Stop** (or **Esc**) ends a long answer and keeps
the part already written; **⧉ copy** next to a message copies its text; the
**↓ latest** button appears when you scroll up and jumps back to the newest
message. **Ctrl+N** starts a new chat, **Ctrl+S** saves it.

## Files here

| Item | What it is |
|---|---|
| `Hirara/` | The tool toolkit project (cloned from GitHub, runs in Docker) |
| `Hirara/docker-compose.slim.yml` | Start/stop the tools |
| `PadaukAgent.py` | The agent script (Ollama ↔ tools) |
| `PadaukChatQt.py` | Primary Qt chat GUI (correct Myanmar shaping) |
| `PadaukChat.py` | Tk fallback GUI |
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
- Readable files: txt, md, csv and other plain text; pdf; docx, pptx, xlsx;
  scanned pdf and images (OCR). Old binary Office formats (**`.doc`, `.xls`,
  `.ppt`**) are not supported — open them once in LibreOffice/Word and save as
  `.docx`/`.xlsx`/`.pptx`. The file picker now offers only supported types.
- Missing images (rate-limited by GHCR): retry `docker pull ghcr.io/lucasdmarshall/hirara-fs:latest`
  and `ghcr.io/lucasdmarshall/hirara-browser:latest` later for file/browser tools.
