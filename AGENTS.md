# AGENTS.md — personal_paduak_agent

Local Burmese AI agent: Ollama model `padauk-gemma:latest` + Hirara tool hub (Docker) + Qt chat GUI.

## Paths & layout

```
~/Desktop/personal_paduak_agent/     ← project root (git repo)
├── Hirara/                          ← shallow git clone of upstream toolkit (mostly read-only)
│   ├── docker-compose.slim.yml      ← START/STOP the tools (local subset of services)
│   └── searxng/settings.yml         ← has a generated secret_key; never publish
├── PadaukAgent.py                   ← CLI agent + shared core (run_tool, tool schemas, NAME_FIX)
├── PadaukChatQt.py                  ← PRIMARY Qt chat GUI (imports PadaukAgent as core)
├── PadaukChat.py                    ← Tkinter fallback GUI (Tk 8.6/X11 cannot shape Myanmar)
├── fonts/                           ← bundled Myanmar fonts (Noto Sans Myanmar, Pyidaungsu)
└── Padauk-Agent-README.md           ← user-facing guide
```

## Commands

```bash
# start/stop tools (hub on 127.0.0.1:8080)
cd ~/Desktop/personal_paduak_agent/Hirara
sg docker -c "docker compose -f docker-compose.slim.yml up -d"
sg docker -c "docker compose -f docker-compose.slim.yml down"

# health + tool list (GET)
curl localhost:8080/health
curl localhost:8080/schemas

# verify agent works (loads 8 GB model; first call can take 30–60 s → long timeouts, 600 s)
python3 ~/Desktop/personal_paduak_agent/PadaukAgent.py "Read ~/docs-test.md and summarize"
```

There is no test suite. Verification = hub `/health` returns `status: ok`, then one real CLI agent run.

## Docker / ghcr gotchas (verified 2026-11)

- **Use `sg docker -c "…"` for every docker command.** `winlintun` is in the `docker` group now, but fresh shells don't inherit it; `sg` avoids a re-login. Plain `docker` fails with permission denied.
- **Don't run `docker compose -f docker-compose.images.yml`** — it references 11 images that ghcr.io intermittently denies to anonymous pulls (`error from registry: denied`). `slim.yml` starts only the images present locally: searxng, websearch, pdf, code, ocr, reader, hub. Missing backends simply don't appear in `/schemas` — that's why `file_read` (`hirara-fs`) and `browser_*` (`hirarabrowser`) tools are absent. Retry pulls one-by-one with `docker pull ghcr.io/lucasdmarshall/hirara-fs:latest`; when one lands, extend `slim.yml` (copy the service block from `docker-compose.images.yml`).
- The hub is loopback-only with auth disabled; `execute_code` mounts the Docker socket (host-root-equivalent). Never publish the port.

## Model quirks (the empty-output bug)

- **Always use `padauk-gemma:latest`, not `padauk-gemma-q8_0:latest`.** The original Modelfile has `PARAMETER num_predict 512`; thinking + answer share that budget, so thinking exhausts it → `content: ""`, `done_reason: "length"`, `eval_count: 512`. Diagnosis: check those three fields in the raw `/api/chat` response.
- **Tool calling only works with `think: true`.** With `think: false` the model never emits `tool_calls` (verified). The GUI/CLI already set this — don't "optimize" it away.
- The 7.5B model invents tool names (`google_search` instead of `web_search`, `read_file`, `pdf_extract`…). `NAME_FIX` in `PadaukAgent.py` normalizes them; extend it if `/schemas` names ever change.
- Context budget is `num_ctx 8192`: `run_tool` truncates tool results to `MAX_TOOL_CHARS` (2500) and web_search to 5 results × 200-char snippets plus a markdown `summary`. Keep any new tool result that big at most.
- `run_turn` in `PadaukAgent.py` owns the model loop (streaming, tool calls, retries); `PadaukChat._worker` and the CLI just call it with an `on_event` callback. A stalled turn (thinking only, no call, no answer) is recovered from the model's own "I will use …" text or retried with a bigger `num_predict` — thinking stays on, never `think: false` as a fix.
- `run_turn(..., cancel=threading.Event())` raises `Cancelled` at stream-chunk, round and tool-call boundaries. The Qt GUI's Stop button/Esc sets it and keeps the partial answer in history; the CLI passes no cancel.

## Tool-call plumbing

- Tool flow: model `/api/chat` with `tools` = hub `GET /schemas` (+ one local `local_file_read`) → execute via hub `POST /call {"name","arguments"}` → feed back as `role: "tool"`.
- Upload arg names differ per tool: `pdf_read`/`pdf_info` use `pdf_base64`; `ocr_read`/`office_read`/`form_extract` use `file_base64`. `PadaukAgent.run_tool` maps a local `path` → base64 via `KEY_MAP`; keep `KEY_MAP` in sync when adding tools.
- `.txt/.md/.csv` are read directly off disk (`local_file_read`, no hub); PDF/Office/scanned files go through the hub containers.
- Readable today (verified 2026-11): txt/md/csv, pdf, docx/pptx/xlsx, scanned pdf and images (PaddleOCR). `office_read` **rejects legacy binary `.doc/.xls/.ppt`** with a save-as-OOXML message — both file pickers omit those extensions; don't add them back.
- Withdrawn attachments are refused in `normalize_args` *before* `path` becomes base64, so upload tools cannot bypass the guard; a path check after normalization only covered `local_file_read`.
- Users' slash commands in the GUI: `/read-file <path> [question]`, `/search <query>` — expanded in `PadaukChat._expand_command`.

## GUI

- **`PadaukChatQt.py` (PySide6) is the primary GUI.** Keep it on PySide6-installed-via
  `pip install --user --break-system-packages PySide6` (done; ~user site, no sudo). Qt runs
  HarfBuzz, so Unicode Burmese renders correctly; Tk 8.6/X11 cannot shape Myanmar and
  never will (proven in report.md §U2 — don't "fix" it by changing fonts).
- Font defense: resolve an installed Myanmar family (`Noto Sans Myanmar` found on this
  machine), else register the bundled `fonts/` TTF via `QFontDatabase.addApplicationFont`,
  else system default. Apply through `QApplication.setFont`, never a stylesheet, or the
  family is lost. See `report-font.md` (the VocApp reference) for the pattern.
- Desktop launcher `~/Desktop/PadaukChat.desktop` execs the **Qt** GUI (updated 2026-11-04).
- Qt quirks: consecutive divs with different margins are separate blocks, but an inline
  `<a>` after `</div>` glues into the previous block — anchors must sit INSIDE their own
  div. Streaming re-renders are throttled to 100 ms (`_render_soon`).
- Answers are Markdown-converted by `md_to_html` (stdlib regexes, no dependency) into
  Qt's limited HTML subset: div/pre/ul/ol/table/blockquote/a/b/i/code only. Qt ignores
  `border-radius`, so bubbles are square; don't add CSS the engine drops.
- `setHtml` lays out on the next event-loop pass, so the scrollbar range is stale
  immediately after it. `_render_now` stores a scroll target and applies it in
  `_apply_scroll` (singleShot 0); auto-scroll is sticky (only if the reader was at the
  bottom) and `↓ latest` appears otherwise.

## GUI (Tkinter fallback) layout traps

- Pack order matters: composer row is packed BEFORE the chat area (side="bottom" first), or the chat Text's default 24-line height pushes the input bar off-window.
- `self.send` must be packed `side="right"` before the expanding input, or it's clipped off the right edge.
- Burmese text needs the "Noto Sans Myanmar" font (detected via `tkfont.families()`; no "Padauk" font is installed). Don't hardcode fonts.
- Double-click launcher: `~/Desktop/PadaukChat.desktop` (Exec → PadaukChat.py; no Terminal).

## Environment

- `sudo` from agent shells can't take a password — group installs (`python3-tk`, adding docker group) require asking the user to run sudo commands themselves; takeover commands are in `Padauk-Agent-README.md`.
- System python is 3.12; the CLI stays stdlib-only. The primary GUI uses PySide6 (installed to `~/.local` with `pip install --user --break-system-packages`; Tk fallback stays stdlib). `Pillow` is installed (icon + screenshots; its raqm mode renders shaped Burmese for test references).
- Locale is zh_CN; file dates display in Chinese — that's normal, not corruption.
- Hirara upstream updates: it's a `--depth 1` clone; `git pull` may fail — prefer re-clone into a temp dir and re-apply the two local files (`docker-compose.slim.yml`, `searxng/settings.yml`).
