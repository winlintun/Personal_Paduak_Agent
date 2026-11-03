#!/usr/bin/env python3
"""
Padauk Chat - Tkinter desktop chat for the local Padauk agent.

* Local Padauk model (Ollama) + Hirara tools (Docker hub)
* Answers stream in; thinking and tool activity show as they happen
* Attach & read: pdf / docx / pptx / xlsx / txt / md / scanned pdf + images
* One-click web search, chat saving, dark theme

Run:  python3 PadaukChat.py
"""
import datetime
import json
import os
import queue
import sys
import threading
import urllib.request

import tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import PadaukAgent as core   # noqa: E402  (run_turn, tools, SYSTEM, ...)

OLLAMA_URL = core.OLLAMA_URL
HUB_URL = core.HUB_URL

# ------------------------------------------------------------- appearance
BG = "#14161f"
PANEL = "#1c1f2b"
PANEL_2 = "#222636"
LINE = "#2e3242"
USER_BG = "#2f5d9e"
ASSIST_BG = "#232738"
TEXT = "#e8eaf2"
SUB = "#9aa3b5"
ACCENT = "#5b8cff"
ACCENT_DIM = "#39415c"
ERR = "#ff6b6b"
OK = "#5dd67a"
WARN = "#ffb84d"

TIPS = ["Tips:  Enter sends · Shift+Enter makes a new line · 📎 attaches a file"]
PLACEHOLDER = "Ask anything — or 📎 attach a document…"
THINK_CHARS = 1100       # thinking preview kept per answer


def first(fams, wanted):
    for w in wanted:
        if w in fams:
            return w
    return "TkDefaultFont"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Padauk — personal agent")
        root.geometry("960x700")
        root.minsize(680, 500)
        root.configure(bg=BG)

        self.history = []              # chat memory, without the system message
        self.q = queue.Queue()         # worker -> UI events
        self.busy = False
        self.tools = []                # tool schemas for the model
        self.names = set()             # their names
        self.tools_ok = False
        self._attached = []            # (path, display name)
        self._placeholder_on = False
        self._thinking = {"text": None, "clipped": False, "shown": False}
        self._answer_row = None
        self._tip = 0

        self._fonts()
        self._header()
        self._composer()          # reserve the bottom slice FIRST…
        self._chat_area()         # …chat fills the remainder and can't push it off-window
        self._greet()

        self.root.after(50, self._poll)
        self.root.after(50, self._refresh_tools)
        threading.Thread(target=self._probe, daemon=True).start()
        self.root.after(60000, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------ fonts
    def _fonts(self):
        fams = set(tkfont.families())
        mm = first(fams, ["Noto Sans Myanmar", "Myanmar Text", "Padauk",
                          "Noto Serif Myanmar"])
        ui = first(fams, ["Noto Sans", "Ubuntu", "DejaVu Sans", "Liberation Sans"])
        mono = first(fams, ["Noto Sans Mono", "DejaVu Sans Mono", "Ubuntu Mono"])
        self.f_text = tkfont.Font(family=mm, size=11)
        self.f_tool = tkfont.Font(family=ui, size=9, slant="italic")
        self.f_ui = tkfont.Font(family=ui, size=10)
        self.f_ui_b = tkfont.Font(family=ui, size=11, weight="bold")
        self.f_small = tkfont.Font(family=ui, size=9)
        self.f_mono = tkfont.Font(family=mono, size=9)

    # ----------------------------------------------------------- header
    def _header(self):
        top = tk.Frame(self.root, bg=PANEL, height=52)
        top.pack(fill="x"); top.pack_propagate(False)

        tk.Label(top, text="🕊  Padauk", bg=PANEL, fg=TEXT,
                 font=self.f_ui_b).pack(side="left", padx=(16, 6))
        self.status = tk.Label(top, text="● starting…", bg=PANEL,
                               fg=SUB, font=self.f_tool, cursor="hand2")
        self.status.pack(side="left", padx=2)
        self.status.bind("<Button-1>", lambda e: self._recheck())

        for label, cmd in (("💾 Save", self._save_chat),
                           ("✨ New chat", self._new_chat)):
            self._btn(top, label, cmd).pack(side="right", padx=(4, 6), pady=9)
        self._btn(top, "⚙ Status reset", self._recheck).pack(side="right",
                                                              padx=(4, 6), pady=9)

        self.meta = tk.Label(top, text="", bg=PANEL, fg=SUB, font=self.f_small)
        self.meta.pack(side="left", padx=12)

    # -------------------------------------------------------- chat area
    def _chat_area(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True)

        self.chat = tk.Text(wrap, wrap="word", bg=BG, fg=TEXT,
                            font=self.f_text, relief="flat", bd=0,
                            padx=16, pady=10, cursor="arrow",
                            selectbackground="#39415c", spacing2=2)
        sb = tk.Scrollbar(wrap, command=self.chat.yview, width=10,
                          bg=PANEL, troughcolor=BG, bd=0,
                          activebackground=LINE)
        self.chat.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.chat.pack(fill="both", expand=True)
        self.chat.configure(state="disabled")
        self.chat.bind("<Button-1>", self._toggle_thinking, add="+")

        tag = self.chat.tag_configure
        tag("user", background=USER_BG, foreground="#ffffff",
            lmargin1=150, lmargin2=150, rmargin=16, spacing1=8, spacing3=8)
        tag("bot", background=ASSIST_BG, foreground=TEXT,
            lmargin2=16, rmargin=150, spacing3=8)
        tag("bot_block", background=ASSIST_BG, foreground=TEXT,
            lmargin1=16, rmargin=150, spacing1=10)
        tag("bot_label", background=ASSIST_BG, foreground=ACCENT,
            lmargin1=16, spacing1=10, font=self.f_ui_b)
        tag("tool", foreground=SUB, background="#191d29",
            lmargin1=32, lmargin2=32, spacing1=3, spacing3=3, font=self.f_tool)
        tag("think", foreground="#7d879e", background="#191d29",
            lmargin1=32, lmargin2=32, spacing1=3, spacing3=3,
            font=self.f_small)
        tag("think_label", foreground="#8b96b3", background="#191d29",
            font=self.f_small, spacing1=4)
        tag("err", foreground=ERR, background="#2a1c21",
            lmargin1=16, lmargin2=16, rmargin=150, spacing1=8, spacing3=8)
        tag("hint", foreground=SUB, font=self.f_ui, spacing1=8, spacing3=6)
        tag("hint_b", foreground=TEXT, font=self.f_ui_b, spacing1=8)
        self._think_elided = False

    # ---------------------------------------------------------- composer
    def _composer(self):
        bottom = tk.Frame(self.root, bg=PANEL)
        bottom.pack(fill="x", side="bottom")

        self.attached = tk.Frame(bottom, bg=PANEL)
        self.attached.pack(fill="x", padx=14, pady=(8, 0))
        self.attached.pack_forget()
        row = tk.Frame(bottom, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(10, 0))

        self.attach_btn = self._btn(row, "📎 Attach", self._attach)
        self.attach_btn.pack(side="left", anchor="s")
        self.search_btn = self._btn(row, "🔎 Web search", self._quick_search)
        self.search_btn.pack(side="left", padx=(8, 0), anchor="s")

        self.send = tk.Button(row, text="Send ➤", command=self._send,
                              bg=ACCENT, fg="#ffffff", activebackground="#4272e0",
                              activeforeground="#ffffff", relief="flat", bd=0,
                              font=self.f_ui_b, cursor="hand2", padx=20,
                              height=2)
        self.send.pack(side="right", fill="y", padx=(10, 0))

        self.input_wrap = tk.Frame(row, bg=PANEL_2, highlightthickness=1,
                                   highlightbackground=LINE,
                                   highlightcolor=ACCENT)
        self.input_wrap.pack(side="left", fill="both", expand=True, padx=10)
        self.inp = tk.Text(self.input_wrap, height=2, width=20, wrap="word",
                           bg=PANEL_2, fg=TEXT, insertbackground=TEXT,
                           font=self.f_text, relief="flat", bd=0,
                           padx=10, pady=8, highlightthickness=0)
        self.inp.pack(fill="both", expand=True)
        self.inp.bind("<Return>", self._on_return)
        self.inp.bind("<Control-Return>", self._on_ctrl_return)
        self.inp.bind("<KeyRelease>", lambda e: self._resize_input())
        self.inp.bind("<FocusIn>", self._focus_in)
        self.inp.bind("<FocusOut>", self._focus_out)

        self.hint = tk.Label(bottom, text=TIPS[0], bg=PANEL, fg=SUB,
                             font=self.f_tool, anchor="w")
        self.hint.pack(fill="x", padx=18, pady=(4, 6))
        self._show_placeholder()

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd, relief="flat",
                         bg=PANEL_2, fg=TEXT, font=self.f_ui, bd=0,
                         activebackground=LINE, activeforeground=TEXT,
                         cursor="hand2", padx=12, pady=7)

    # ------------------------------------------------------------- UI io
    def _emit(self, tag, text):
        self.q.put(("text", tag, text))

    def _write(self, tag, text):
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _poll(self):
        chat_dirty = False
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "text":
                self._write(item[1], item[2])
            elif kind == "status":
                self.status.configure(text=item[1], fg=item[2])
            elif kind == "thinking":
                self._stream_thinking(item[1])
            elif kind == "answer":
                self._stream_answer(item[1])
                chat_dirty = True
            elif kind == "round":
                self.status.configure(text=f"● thinking · round {item[1]}", fg=SUB)
            elif kind == "tok":
                self.meta.configure(text=item[1])
            elif kind == "done":
                self._finish()
        if chat_dirty:
            self.chat.see("end")
        self.root.after(50, self._poll)

    # -------------------------------------------------------- streaming
    def _stream_thinking(self, text):
        # buffer only: thinking is revealed (or not) when the answer starts,
        # because a half-shown chain of thought is just noise
        st = self._thinking
        if st["text"] is None:
            st.update(text="", clipped=False)
        if not st["clipped"]:
            st["text"] += text
            if len(st["text"]) >= THINK_CHARS:
                st["text"] += " …"
                st["clipped"] = True
        self.status.configure(text="● thinking…", fg=SUB)

    def _answer(self, text):
        self.chat.configure(state="normal")
        if self._answer_row is None:
            self._flush_thinking()
            self._answer_row = True
            self._bot_start = self.chat.index("end-1c")
            self._bot_buf = ""
            self.chat.insert("end", "🕊 ", "bot_label")
        self._bot_buf += text
        self._render_bot()
        self.chat.configure(state="disabled")
        self.status.configure(text="● writing…", fg=SUB)

    def _render_bot(self):
        # every line ends in \n so no stale trailing row keeps the block
        # background, and the head glyph sits inside the block's first line
        row = int(self._bot_start.split(".")[0])
        parts = ("🕊 " + self._bot_buf.rstrip("\n")).split("\n")
        end_row = int(self.chat.index("end-1c").split(".")[0])
        self.chat.delete(self._bot_start, f"{end_row}.end")
        pos = row
        for part in parts:
            self.chat.insert(f"{pos}.0", part + "\n", "bot")
            pos += 1
        self.chat.tag_add("bot_label", f"{row}.0", f"{row}.2")
        self.chat.tag_add("bot_block", f"{row}.0", f"{pos}.0")

    def _stream_answer(self, text):
        self._answer(text)

    def _flush_thinking(self):
        """Show the thinking gathered so far, once, above the answer."""
        st = self._thinking
        if st.get("shown") or not st.get("text"):
            return
        st["shown"] = True
        start = self.chat.index("end-1c")
        self.chat.insert("end", "💭 thinking — click to hide\n", "think_label")
        self.chat.mark_set("think_end", "end-1c")
        self.chat.insert("end", st["text"] + "\n", "think")
        self.chat.tag_add("think_label", start, self.chat.index("think_end"))

    def _toggle_thinking(self, event):
        index = self.chat.index(f"@{event.x},{event.y}")
        tags = self.chat.tag_names(index)
        if "think" not in tags and "think_label" not in tags:
            return
        self._think_elided = not self._think_elided
        self.chat.tag_configure("think", elide=self._think_elided)
        label = getattr(self, "_think_label_id", None)
        if label and not self._think_elided:
            self.chat.tag_remove("think_label", label, f"{label}+1c")

    # ---------------------------------------------------------- helpers
    def _resize_input(self):
        lines = int(self.inp.index("end-1c").split(".")[0])
        self.inp.configure(height=min(max(lines, 1), 6))

    def _on_return(self, event):
        self._send()
        return "break"

    def _on_ctrl_return(self, event):
        self.inp.insert("insert", "\n")
        self._resize_input()
        return "break"

    def _show_placeholder(self):
        if self.inp.get("1.0", "end-1c"):
            return
        self._placeholder_on = True
        self.inp.configure(fg=SUB)
        self.inp.insert("1.0", PLACEHOLDER)

    def _focus_in(self, event):
        if self._placeholder_on:
            self._placeholder_on = False
            self.inp.delete("1.0", "end")
            self.inp.configure(fg=TEXT)

    def _focus_out(self, event):
        self._show_placeholder()

    def _input_text(self):
        if self._placeholder_on:
            return ""
        return self.inp.get("1.0", "end-1c").strip()

    def _clear_input(self):
        self._placeholder_on = False
        self.inp.delete("1.0", "end")
        self.inp.configure(fg=TEXT)
        self._resize_input()

    def _finish(self):
        self.busy = False
        self.send.configure(text="Send ➤", state="normal", bg=ACCENT)
        self._flush_thinking()          # a turn that only thought still shows it
        self._answer_row = None
        self._thinking = {"text": None, "clipped": False, "shown": False}
        self.chat.configure(state="normal")
        self.chat.insert("end", "\n")
        self.chat.configure(state="disabled")
        self.inp.focus_set()
        threading.Thread(target=self._probe, daemon=True).start()

    # ------------------------------------------------------------- send
    def _send(self):
        if self.busy:
            return
        text = self._input_text()
        if not text:
            return
        text = self._expand_command(text)
        self._clear_input()
        self.history.append({"role": "user", "content": text})
        self._emit("user", text + "\n\n")
        self._attached.clear()
        self._render_attached()

        self.busy = True
        self.send.configure(state="disabled", bg=ACCENT_DIM, text="…")
        self.status.configure(text="● contacting model…", fg=SUB)
        threading.Thread(target=self._worker, args=(list(self.history),),
                         daemon=True).start()

    # ------------------------------------------------------- attachments
    def _attach(self):
        path = filedialog.askopenfilename(
            title="Choose a document for the agent to read",
            initialdir=os.path.expanduser("~"),
            filetypes=[("Documents",
                        "*.pdf *.docx *.pptx *.xlsx *.txt *.md *.csv "
                        "*.png *.jpg *.jpeg *.webp *.doc"),
                       ("All files", "*.*")])
        if not path:
            return
        if not self._input_text():
            if self._placeholder_on:
                self._clear_input()
            self.inp.insert("1.0", "Summarize this document in Burmese.")
        else:
            self.inp.insert("insert", f" {path}")
        self._attached.append((path, os.path.basename(path)))
        self._render_attached()
        self._resize_input()
        self.inp.focus_set()

    def _render_attached(self):
        for child in self.attached.winfo_children():
            child.destroy()
        if not self._attached:
            self.attached.pack_forget()
            return
        self.attached.pack(fill="x", padx=14, pady=(8, 0))
        self.attached.lift()
        for path, name in self._attached:
            chip = tk.Frame(self.attached, bg=PANEL_2)
            chip.pack(side="left", padx=(0, 6))
            tk.Label(chip, text=f"📄 {name}", bg=PANEL_2, fg=TEXT,
                     font=self.f_small).pack(side="left", padx=(8, 4), pady=3)
            close = tk.Label(chip, text="✕", bg=PANEL_2, fg=SUB,
                             font=self.f_small, cursor="hand2")
            close.pack(side="left", padx=(0, 8))
            close.bind("<Button-1>", lambda e, p=path: self._unattach(p))

    def _unattach(self, path):
        self._attached = [a for a in self._attached if a[0] != path]
        text = self.inp.get("1.0", "end-1c").replace(path, "").strip()
        self._clear_input()
        if text:
            self.inp.insert("1.0", text)
        self._render_attached()

    def _quick_search(self):
        if self.busy:
            return
        if not self._input_text():
            self._clear_input()
            self.inp.insert("1.0", "/search ")
        else:
            self.inp.insert("insert", " ")
        self.inp.focus_set()
        self.inp.focus_force()

    # ------------------------------------------------------------ worker
    def _worker(self, history):
        events = _Events(self.q)
        try:
            messages = [{"role": "system", "content": core.SYSTEM}] + history
            core.run_turn(messages, self.tools, stream=True, on_event=events)
            answer = messages[-1].get("content", "")
            if answer.strip():
                self.history.append({"role": "assistant", "content": answer})
            elif not events.printed:
                self.q.put(("text", "err",
                            " ⚠ The model returned an empty answer.\n\n"))
        except Exception as exc:                          # noqa: BLE001
            self.history.pop()                 # drop the turn the model never saw
            self.q.put(("text", "err", f" ⚠ {exc}\n\n"))
        finally:
            self.q.put(("done",))

    # ------------------------------------------------------------ status
    def _refresh_tools(self):
        def load():
            tools = core.load_tool_schemas()
            names = {t["name"] for t in tools}
            self.tools, self.names = tools, names
            core.AVAILABLE = names
            self.q.put(("tok", "tools loaded" if tools else "tools: hub offline"))
        threading.Thread(target=load, daemon=True).start()

    def _probe(self):
        hub = model = False
        try:
            urllib.request.urlopen(f"{HUB_URL}/health", timeout=3).read()
            hub = True
        except Exception:                                 # noqa: BLE001
            pass
        try:
            body = json.dumps({"model": core.MODEL}).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/show", data=body,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
            model = True
        except Exception:                                 # noqa: BLE001
            pass
        self.tools_ok = hub and model
        if hub and model:
            self.q.put(("status", "● ready", OK))
        elif model:
            self.q.put(("status", "● model ok · tools down", WARN))
        elif hub:
            self.q.put(("status", "● tools ok · model down", WARN))
        else:
            self.q.put(("status", "● offline", ERR))
        if not hub:
            self._refresh_tools()          # schemas may load now that the hub is up

    def _recheck(self):
        self.status.configure(text="● checking…", fg=SUB)
        threading.Thread(target=self._probe, daemon=True).start()
        self._refresh_tools()

    def _tick(self):
        if not self.busy:
            threading.Thread(target=self._probe, daemon=True).start()
        self._tip = (self._tip + 1) % len(TIPS)
        self.hint.configure(text=TIPS[self._tip])
        self.root.after(60000, self._tick)

    # -------------------------------------------------------------- misc
    def _new_chat(self):
        self.history.clear()
        self._thinking = {"text": None, "clipped": False, "shown": False}
        self._answer_row = None
        self._attached.clear()
        self._render_attached()
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self._clear_input()
        self._greet()
        self._recheck()

    def _save_chat(self):
        if not self.history:
            self._emit("hint", "Nothing to save yet.\n")
            return
        default = datetime.datetime.now().strftime("padauk-chat-%Y%m%d-%H%M.md")
        path = filedialog.asksaveasfilename(
            initialdir=BASE, initialfile=default, defaultextension=".md",
            filetypes=[("Markdown", "*.md")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            for m in self.history:
                role = {"user": "🧑 You", "assistant": "🕊 Padauk",
                        }.get(m["role"], m["role"])
                f.write(f"**{role}:**\n\n{m.get('content', '')}\n\n---\n\n")
        self._emit("text", "hint", f"Saved: {path}\n")

    def _greet(self):
        self.chat.configure(state="normal")
        self.chat.insert("end", "မင်္ဂလာပါ! ကျွန်မ Padauk ပါ။\n", "hint_b")
        self.chat.insert("end",
                         "– 📎 Attach a document, then Send\n"
                         "– 🔎 Web search: type a topic, press Enter\n"
                         "– Enter sends · Shift+Enter starts a new line\n",
                         "hint")
        self.chat.configure(state="disabled")

    def _expand_command(self, text: str) -> str:
        """Turn button shortcuts into natural instructions."""
        first_line, _, rest = text.partition("\n")
        head, _, tail = first_line.partition(" ")
        if head == "/read-file":
            path = tail.strip()
            ask = rest.strip() or "summarize it"
            if not path:
                return "Please attach a file with the 📎 button first."
            return (f"Read the local file at this exact path: {path}\n"
                    f"Use local_file_read / pdf_read / office_read / ocr_read "
                    f"as appropriate (pass that whole path as path). "
                    f"Then {ask}.")
        if head == "/search":
            q = tail.strip() or rest.strip()
            if not q:
                return "Which topic should I search the web for?"
            return (f"Use the web_search tool with a short keyword query "
                    f"about: {q}\nThen answer from the results.")
        return text

    def _close(self):
        self.root.destroy()


class _Events:
    """PadaukAgent on_event -> queue events for the UI thread."""

    def __init__(self, q):
        self.q = q
        self.printed = False

    def __call__(self, kind, *data):
        if kind == "thinking":
            self.q.put(("thinking", data[0]))
        elif kind == "answer":
            self.printed = True
            self.q.put(("answer", data[0]))
        elif kind == "notice":
            self.q.put(("text", "tool", f"   ↻ {data[0]}\n"))
        elif kind == "round":
            self.q.put(("round", data[0]))
        elif kind == "tool_call":
            name, args = data
            preview = args.get("query") or args.get("path") or args.get("url") or ""
            if isinstance(preview, list):
                preview = ", ".join(map(str, preview))
            preview = str(preview)
            if len(preview) > 70:
                preview = preview[:70] + "…"
            icon = {"web_search": "🔎", "web_fetch": "🌐", "local_file_read": "📄",
                    "pdf_read": "📕", "pdf_info": "📕", "office_read": "📄",
                    "ocr_read": "🖼", "execute_code": "🧮"}.get(name, "⚙")
            self.q.put(("text", "tool", f"{icon} {name}  {preview}\n"))
        elif kind == "tool_result":
            name, res = data
            body = res.get("result") if isinstance(res, dict) else None
            error = body.get("error") if isinstance(body, dict) else None
            if error:
                self.q.put(("text", "tool", f"   ⚠ {name} failed: {error}\n"))
                return
            if isinstance(body, dict) and "results" in body:
                n = len(body.get("results") or [])
                cached = " · cached" if body.get("cached") else ""
                self.q.put(("text", "tool", f"   {n} results{cached}\n"))


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
