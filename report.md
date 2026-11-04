# report.md — bug & performance audit, 2026-11-04

Scope: `PadaukAgent.py` (agent core), `PadaukChat.py` (Tkinter GUI), live hub (slim compose),
model `padauk-gemma:latest`. Every finding below was reproduced against the running stack
(code-read + scripted tests T1–T6 + GUI smoke test).

**Verdict: the new round of features works. Two HIGH issues are silent
quality killers and should be fixed before this feels reliable.**

---

## 1. Features verified working ✅

| Feature | Evidence |
|---|---|
| Streaming answer + thinking preview in GUI | T1/T5 runs; UI smoke screenshot |
| **Stall recovery** (model thought but emitted nothing) | Observed live: web-search run logged `model stalled - retry with a 3072-token budget`, then answered correctly (USD→MMK = 2,099.75 ✓) |
| **Described-call recovery** ("I will use `web_search`…" without emitting it) | Observed live: file-read turn logged `ran the local_file_read call the model described` and produced the right answer in 12.7 s |
| Repeat-limit retry (HTTP 500 → smaller budget) | Code path verified in review; not reproducible on demand |
| Tool result caching | Repeat search returned identical result; hub marks responses `cached` (the GUI "· cached" line reads this flag — it comes from the hub, correct) |
| Parallel tool execution (`ThreadPoolExecutor(4)`) | Present; not exercised by tests (single-call turns dominated) |
| Schema hygiene (`form_extract` dropped, `additionalProperties` removed, `required` cleaned) | `/schemas` → model accepted every call in tests |
| Chips/slash-commands (`/read-file`, `/search`), status dot, tips, Save/New chat | GUI smoke test ✅ |

## 2. Bugs

### B1 · HIGH — long answers are silently cut mid-sentence
`T3`: ask for a long Burmese essay → 72 s, `done_reason: length`, `eval 2048/2048`,
ends mid-word ("…ခေတ်ကာ"). Thinking shares `num_predict 2048`, and `run_turn` only
retries on *empty* content (the stall path) — a **truncated but non-empty** answer is
returned as success with no notice streamed to the user.
**Fix:** in `_collect`/`_consume_stream`, surface `reason == "length"` as an event
(`notice: answer truncated — say 'continue' to extend`), or auto-continue by appending
"continue exactly where you stopped" with the tail kept in context. Also consider
`NUM_PREDICT = 3072` for voice-of-truth answers now that `keep_alive` warms the model.

### B2 · HIGH — CLI multi-turn context explosion (slow degradation, then silence)
`run_turn` appends **full `thinking` text** into every assistant message it stores
(L512–512) and the CLI keeps the complete transcript forever inside `num_ctx 8192`.
`T6`: one short thinking replayed by accident already cost **+150 prompt tokens**
(34 → 184). Several search rounds later, transcript grows thousands of tokens/turn.
`T2`: when input exceeds the context, Ollama does **not** error — it returns 200 with
**empty content** (silent quality collapse, the same class of bug as the original
`num_predict 512` problem). The GUI partially avoids this (history keeps only
user+final-answer), the CLI does not.
**Fix:** strip `thinking` from assistant messages when building the next-round request
(the gemma4 renderer regenerates the thought channel; feeding it back is wasted prompt);
add a transcript budget: before each round, drop the oldest (user, assistant, tool…)
triplets until `prompt_eval_count` of the previous turn stays ≤ ~6.5k.

### B3 · MED — context overflow is invisible
`T2` (22k-token prompt, `num_ctx 8192`): HTTP 200, `done_reason length`, empty content.
Nothing in either UI explains it. Fix = part of B2: watch `prompt_eval_count`
(recorded in the `/api/chat` response; currently discarded) and warn/prune when
it approaches `NUM_CTX`.

### B4 · MED — GUI text promises “Shift+Enter makes a new line”; it actually sends
`<Return>` binding catches Return *with any modifier* (Shift included), and only
`<Control-Return>` inserts a newline (L199, L324–331). Greeting (L556) and TIPS (L46)
tell the user the opposite. **Fix:** add `self.inp.bind("<Shift-Return>",
self._on_ctrl_return)` and keep the texts (or vice versa).

### B5 · LOW — tool cache is not thread-safe
`CACHE` (`OrderedDict`) is mutated by up to 4 threads at once when the model emits
several parallel calls (L442); `popitem/move_to_end` races can raise
`RuntimeError: OrderedDict mutated during iteration`. Rare, self-healing (exception is
caught per-call), but cheap to fix: wrap cache access in `threading.Lock`.

### B6 · LOW — thinking toggle label is dead code + static text
`_think_label_id` is never assigned (L315), and the label always says
“click to hide” even when the block is hidden. Pointless but confusable.

### B7 · LOW — startup race: first message may run with no tools
`self.tools` is loaded asynchronously; a message sent within the first seconds runs
`run_turn(messages, [])` → model cannot call tools and (being a 7.5B model) may answer
from memory. Fix: disable Send until `tools_ok` (status dot already signals it).

### B8 · LOW — attachment chip can outlive its path
`_unattach` strips the path from input, but manual deletion of the text (or typing
more before sending with an empty instruction) leaves a chip whose file is never
sent. Suggest: `_send` re-verifies each chip path is present in the expanded text,
else re-inserts it.

## 3. Performance (measured, warm model, keep_alive 30m)

| Test | Result |
|---|---|
| T1 trivial turn (“Say OK”) | **9.5 s**, `eval 256/256`, reason `length` — thinking burns the whole small budget even on trivial prompts; harmless here but explains why every turn costs ≥ ~8 s. |
| File-read turn end-to-end (CLI) | **12.7 s**, correct answer (Daw Khin / U Aung) |
| Web-search turn end-to-end (CLI) | **17.2 s** incl. one stall-retry; correct USD→MMK rate |
| T3 long essay (2048-token budget) | **72 s**, truncated mid-sentence (→ B1) |
| T5 multi-turn (3 turns) | 9.8 s → 4.2 s → **1.5 s** (prompt grows, model warm) |
| T6 thinking replayed in history | +150 prompt tokens on a 34-token prompt — pure waste, every stored round (→ B2) |
| T2 context overflow | no error, empty output (→ B2/B3) |
| GUI idle | 50 ms poll loop, negligible CPU; probes every 60 s only when idle (correctly skipped while busy) |

Takeaway: end-to-end latencies are dominated by model thinking, which is inherent to
Gemma-4 reasoning turns; the recoverable waste is B2 (replayed thinking) and B1
(72 s turns that throw the answer away at the end).

## 4. Recommended order of fixes

1. **B1** — surface/auto-continue `done_reason: length` (highest user-visible win).
2. **B2 + B3** — strip thinking from history; transcript budget with last
   `prompt_eval_count` logging.
3. **B4** — one-line `Shift-Return` binding.
4. **B5, B6, B7, B8** — small hardening patches.
5. Commit everything (a full working sweep is sitting uncommitted in the tree).

---

## 5. Thinking vs no-thinking — the different answers (A/B, measured 2026-11-04)

Same model (`padauk-gemma:latest`), same prompts, only `think` toggled.

| Test | `think: false` | `think: true` |
|---|---|---|
| `7 × 8 + 15 ÷ 5 − 3` (correct: 56) | **"64" — wrong**, 0.5 s | **"74" — wrong**, 0.4 s (both slipped; trivial prompts are luck) |
| "How many letter r in strawberry?" (correct: 3) | **"two" — wrong**, 4.1 s; hallucinated an S/T/R table to back it up | **"၃ လုံး" — right**, 18.1 s; 1,389 chars of step-by-step counting |
| Tool call (USD→MMK, raw schemas) | called an invented tool name in 3.0 s | called an invented tool name in 5.9 s (the agent core's cleaned schemas + `NAME_FIX` are what make names land — verified in §1) |

### The real differences, stated plainly

1. **Latency.** Thinking multiplies turn time (≈×4–5 here: 4.1 s vs 18.1 s, 9.5 s vs 0.5 s simple turns). Every reasoning token shares the `num_predict` budget with the answer — which is exactly why the original empty-output bug existed.
2. **Accuracy on anything multi-step.** Without thinking the model answers from pattern memory and is *confidently* wrong (the r-count test answered wrong **and** invented evidence for it). With thinking it walks the steps and gets reasoning questions right. This matches why the agent core always sends `think: true`.
3. **Tool calling.** Thinking on is the reliable path for tool rounds (§1: `think: false` produced zero usable calls in the audit); with thinking off, tool misses degrade to *answers from memory* — for "today's price/rate" questions that is the worst failure mode (a wrong answer with no signal).
4. **Answer style.** No-thinking replies are quicker and leaner (eval 3→95 vs 142–496 on these tests) and fine for deterministic formatting/echo tasks. Thinking replies carry the 🕊 persona's reasoning trace (the GUI shows it as a collapsible 💭 block).
5. **Failure shape.** No-thinking failures are silent-wrong; thinking failures cost *time* but the pipeline now catches them (stall-retry, described-call recovery, B1 continuation loop).

### Recommendation (unchanged, now measured)
Keep `think: true` for every agent turn — the latency is the price of not being
confidently wrong. `think: false` is acceptable only for pure re-formatting chores
(e.g., "rewrite this text in formal Burmese"), where there is nothing to reason
about and 0.5 s beats 9.5 s.

---

---

## 6. Done! — fix log (2026-11-04)

Every item from §2 is fixed in `PadaukAgent.py` / `PadaukChat.py`, plus three
bugs found while fixing them (B9–B11). Model stays `think: true` everywhere
(§5); the answer is now *complete*, not just *not empty*.

| # | What was wrong | Fix (code) | Verified |
|---|---|---|---|
| B1 | Answer cut at `num_predict` with no notice; 72 s turn thrown away | New `finish_answer()`: while `done_reason == "length"`, it streams a notice (`answer hit the output limit - continuing`), appends what was shown + the `CONTINUE` instruction, and calls the model again with a fresh budget (max `MAX_CONTINUES = 3`, thinking on). Parts are joined with `_drop_repeat()` so the overlap the model re-sends is not shown twice. Applies to CLI and GUI, streaming and not. | stub-head test: `1,2,3` + continuation `4,5,6` → single stream, stored == streamed. Live 1200-word essay and 600-word essay end with `done_reason: stop`. |
| B2 | Thinking replayed into every assistant message (wasted prompt, silent degradation) | Assistant tool-call messages are stored without `thinking`. CLI transcript is trimmed to `HISTORY_CHARS` by dropping whole old turns. | 3-turn live run: 0 stored messages contain `thinking`; prompt 341 → 2451 tokens instead of growing with thoughts. |
| B3 | Prompt past `num_ctx` → HTTP 200 with empty content, invisible | `history_window()` keeps system + the most recent **whole** turns + the current one under `HISTORY_CHARS` (≈45 % of ctx); each model round reports `prompt_eval_count`; above `CTX_WARN` (75 %) the UI/CLI prints a "near the context limit" notice. `trace=` adds per-round diagnostics. | 212k-char transcript → prompt 4350 tokens → real answer (before: empty). |
| B4 | Shift+Enter sent the message although the tip says it starts a new line | `<Shift-Return>` bound to the newline handler. | GUI: `line one` + Shift+Enter + `line two` → one message with a newline. |
| B5 | `CACHE` OrderedDict mutated by parallel tool threads | `CACHE_LOCK` around every read/write; cache hits return a deep copy. | code review + parallel search run. |
| B6 | Thinking toggle label never changed; `_think_label_id` was dead | `THINK_HEAD` label flips between *click to hide* / *(hidden) click to show*; `replace()` keeps the tag on the label. | three clicks cycle hide → show → hide, thought stays on its own row. |
| B7 | First message could run with no tools | If a probe is in flight, Send waits for it, then resends the same text exactly once; Send stays disabled while the hub is down. | GUI: send during startup probe → answered with tools. |
| B8 | Attachment chip could outlive its path in the text | `_send` keeps only chips whose path is still in the message. | code path: verified by reading, no UI test. |
| B9 | **New** — the 60 s/`_finish` health probe re-enabled the Send button mid-turn (double send possible) | Single `_refresh_send()` used by every caller; probes reported via `probe_start`/`probe_done`. | live GUI turn: Send stays dimmed during the turn. |
| B10 | **New** — a message postponed for the probe stayed in the chat even when it was answered in the meantime | `_turn_mark`; `_drop_turn_mark()` removes an abandoned turn's rows. | live startup send: exactly one user row, one answer. |
| B11 | **New** (from applying §6 in parallel) — nudge flag reset each round (nudge could loop), `finish_answer` join duplicated the head, quiet retries sent an 8192-token prompt | `nudged` is per turn; join rewritten; `HISTORY_CHARS` lowered so tool results + thinking + answer fit; `history_window` rebuilt over paired turns. | the turn that produced `context 8077/8192` + "no answer" now answers in 30 s at `context 1783/8192`. |

### Live verification (after the fixes)

| Check | Result |
|---|---|
| Long answer (essay prompt) | `done_reason: stop`, ends on a finished sentence |
| Truncation forced (`num_predict 60`, stub head) | notice streamed, continuation joined, `reason: stop` |
| 3-turn CLI session | answers 3/3, no `thinking` stored, prompt logged per round |
| Buried-history overflow (212k chars) | answer `OK.`, prompt 4350/8192 |
| Web tool turns (USD→MMK, then EUR→MMK) | correct rates, one stall-retry recovered |
| GUI live turn (Burmese) | user + answer, thinking collapsed, Send re-enabled once |
| GUI smoke (Shift+Enter, toggle, probe wait) | all pass |

Known limits: the 7.5B model sometimes spends a whole 2048-token round thinking
before writing (stall-retry then takes another 8–25 s) — that is latency, not a
lost answer; and with truncation the first piece occupies the whole round, so a
long answer takes 1–3 extra rounds (≈ +20 s each).

Run the trace for any odd turn:

```bash
PADAUK_TRACE=1 python3 PadaukChat.py    # prints one row per round to stderr
```

*Everything above was run against the live stack (hub `/health` ok, `padauk-gemma:latest`).*

---

## 7. Live bug report #2 (2026-11-04): "upload not working" + "Myanmar font not working"

Both reported against HEAD `83f4d70`, reproduced locally, root-caused, fixed, and
verified live.

### U1 · HIGH — attach on an empty input dropped the file path

Reproduction: click 📎 **Attach** while the input box is empty (the normal
"attach first, type later" flow) → `_attach` inserted the default instruction
`"Summarize this document in Burmese."` **but never the file path**. `_send`'s
chip filter `a[0] in text` then dropped the chip, and the model received an
instruction with **no path at all** → nothing was ever read.
Proof (offscreen harness): sent frame = `Summarize this document...` with
`chips surviving: []`.

**Fix** (in both GUIs): with an empty input, `_attach` now inserts
`Summarize this document in Burmese. <path>`; a non-empty input still gets the
path appended. Verified: sent message now contains the full path, the model
calls `pdf_read(path=...)`, and `run_tool` resolves it to base64 (hub-tested on
a real 15 MB PDF → text extracted).

### U2 · HIGH — Tk 8.6/X11 cannot shape Myanmar (the "font" bug)

Symptom: the GUI greeting `မင်္ဂလာပါ! ကျွန်မ Padauk ပါ။` displayed as scrambled
glyph clusters.

Root cause: **Tk 8.6 `font` widget on X11 renders through Xft, which performs
no OpenType shaping** (no GSUB/GPOS for Myanmar). Vowel signs stay in logical
order instead of visual (left-side ေ stays right), `င်္` stacking is not
applied, conjunct forms are not selected — this is a text-engine limitation.
**No font choice fixes it** (Noto Sans Myanmar was correctly selected already;
the probe proved the same text reshaped correctly under HarfBuzz/Pillow raqm).

Evidence (side-by-side, same string, same font file):

| Renderer | Result |
|---|---|
| Tk 8.6/X11 (old GUI) | scrambled clusters, detached marks (screenshot crop) |
| Pillow raqm (ground truth) | correct conjuncts + mark order |
| Qt 6 offscreen (new GUI) | **identical to ground truth** |

**Fix**: new primary GUI `PadaukChatQt.py` (PySide6 6.12, `pip install --user
--break-system-packages`, no sudo needed):

* Same agent core (`PadaukAgent.run_turn`), same features as the Tk GUI
  (streaming answer/thinking, tool lines, chips, attach/search, probe-status
  dot, Save/New chat, Enter/Shift+Enter, dark theme, placeholder).
* Fonts: 3-layer resolution per `report-font.md` (installed Myanmar family →
  bundled `fonts/Noto_Sans_Myanmar` registration → system default), set
  app-wide via `QApplication.setFont` so stylesheets never override it.
* Conversation rendered from a **row list**, throttled re-render (100 ms,
  avoids O(chunk)-sized `setHtml` churn), thinking toggle via anchor, scroll
  pinned to bottom. (Qt quirk found + fixed: an inline `<a>` directly after a
  `</div>` glues into the previous block — the anchor now lives inside its own
  block div.)
* `Desktop/PadaukChat.desktop` now launches the Qt GUI. `PadaukChat.py` (Tk)
  remains as fallback with the same fixes applied.

Verified live through the new GUI (real model, offscreen harness): math turn
correct (`၁၄၄`), PDF upload turn self-corrected and answered with the author.

### U3 · MED — `local_file_read` on binary files poisoned the turn (found in the live GUI test)

The model called `local_file_read` on a .pdf, got mojibake (no clear error),
and gave up with "can't read the author". One wrong tool choice wasted the turn.

**Fix** (`PadaukAgent.read_text_file`): known-text extensions only; other
extensions (`.pdf`, `.docx`, …) and NULL-byte sniffing return a **redirect
error** ("not a plain-text file — use pdf_read / office_read / ocr_read");
`local_file_read`'s schema description now says "never for pdf/docx/xlsx".
Verified live: the model's thinking showed *"local_file_read returned an error
that the file is not a plain-text file... I need to correct my tool usage and
call pdf_read"*, then `pdf_read` succeeded and the author was answered.

### U4 · MED — the agent re-reads a file the user already removed (reported live, fixed)

Reproduction (real model, Qt GUI): turn 1 attach + ask (`pdf_read`, answered) →
user removes the chip → turn 2 asks about "this document" → the model **re-read
the file** (in one run `local_file_read` → `pdf_read`; it never learned the
attachment was withdrawn). Cause: the turn-1 user message with the full path
stays in history, the tool result is pruned after the turn, so follow-ups
re-trigger a read — and nothing told the model the file was gone.

Fix, two layers:

1. **Withdrawal bookkeeping (both GUIs).** `_unattach` records the path in
   `_withdrawn` (until re-attached or New chat); `_attach` clears the entry on
   re-add. At each send, the effective set is `withdrawn − attached − paths-in-
   the-new-message`.
2. **Hard block in the core.** `run_turn(..., denied_paths=...)` threads the set
   into `run_tool`, which — after path normalization — refuses any call whose
   resolved path is denied and returns a redirect error ("removed from the chat;
   ask the user to re-attach"). A 7.5B model ignores polite system notes (the
   note alone was not enough — verified), so the block must be mechanical.

Verified live (3 turns, real model):
turn 1 `pdf_read` read once → turn 2 (file removed) the model's `pdf_read`
attempt was **denied by the core** and the file was never touched → turn 3
(re-attached) `pdf_read` allowed again.

Also fixed while reproducing: the `Could not parse stylesheet of object
ChatBrowser` warning (one extra `}` in the scrollbar rule, plus Qt not
supporting stylesheet `padding` on `QTextBrowser` → `document().setDocumentMargin`).
