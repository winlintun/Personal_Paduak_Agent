#!/usr/bin/env python3
"""
Padauk Chat (Qt) - PySide6 desktop chat for the local Padauk agent.

Why Qt: Tk 8.6 on X11 cannot shape Myanmar script (its Xft text engine has
no OpenType shaping), so Unicode Burmese renders with scrambled glyph order.
Qt 6 uses HarfBuzz and renders it correctly. Same agent core (PadaukAgent),
same features as PadaukChat.py (which stays as the Tk fallback).

Run:  python3 PadaukChatQt.py
"""
import datetime
import json
import os
import queue
import sys
import threading
import urllib.request

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QPushButton, QTextBrowser,
                               QTextEdit, QVBoxLayout, QWidget)

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import PadaukAgent as core   # noqa: E402  (run_turn, tools, SYSTEM, ...)

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
THINK_CHARS = 1100

MM_FONTS = ("Noto Sans Myanmar", "Noto Sans Myanmar UI", "Myanmar Text",
            "Padauk", "Myanmar3", "Pyidaungsu")
UI_FONTS = ("Noto Sans", "Ubuntu", "DejaVu Sans", "Liberation Sans")

THINK_LABEL = "💭 thinking · click to hide"
THINK_HIDDEN_LABEL = "💭 thinking (hidden) · click to show"


def resolve_myanmar_family():
    """Layer 1: an installed Unicode Myanmar font. Layer 2: the bundled
    fonts/ copy. Layer 3: system default."""
    installed = set(QFontDatabase.families())
    for fam in MM_FONTS:
        if fam in installed:
            return fam
    local = os.path.join(BASE, "fonts", "Noto_Sans_Myanmar", "static",
                         "NotoSansMyanmar-Regular.ttf")
    if os.path.isfile(local):
        fid = QFontDatabase.addApplicationFont(local)
        if fid != -1:
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                return fams[0]
    return QFont().defaultFamily()


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Chip(QFrame):
    """Attachment chip with a remove button."""

    def __init__(self, path, on_remove):
        super().__init__()
        self.path = path
        box = QHBoxLayout(self)
        box.setContentsMargins(8, 2, 2, 2)
        box.setSpacing(4)
        style = (f"color:{TEXT}; background:{PANEL_2}; border:none;"
                 "font-size:12px;")
        name = os.path.basename(path)
        if len(name) > 26:
            name = name[:23] + "…"
        label = QLabel(f"📄 {name}")
        label.setStyleSheet(style)
        close = QPushButton("✕")
        close.setStyleSheet(style + "padding:2px 6px; border-radius:4px;")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(on_remove)
        box.addWidget(label)
        box.addWidget(close)


class InputEdit(QTextEdit):
    """Multi-line input where Enter sends and Shift+Enter makes a newline."""

    def __init__(self, send):
        super().__init__()
        self._send = send
        self.setPlaceholderText(PLACEHOLDER)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                self.insertPlainText("\n")
            else:
                self._send()
            return
        super().keyPressEvent(event)


class ChatBrowser(QTextBrowser):
    """The whole conversation, rendered as HTML with a Myanmar-capable font."""

    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"QTextBrowser {{ background:{BG}; color:{TEXT};"
            "padding:12px; }}"
            f"QScrollBar:vertical {{ background:{BG}; width:10px; }}"
            f"QScrollBar::handle:vertical {{ background:{LINE};"
            "border-radius:5px; min-height:30px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical "
            "{ height:0; }")
        self.setContextMenuPolicy(Qt.DefaultContextMenu)


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.history = []              # chat memory, without the system message
        self.q = queue.Queue()         # worker -> UI events
        self.busy = False
        self.tools = []                # tool schemas for the model
        self.tools_ok = False
        self.hub_down = False
        self._probes = 0
        self._probe_lock = threading.Lock()
        self._waiting = False
        self._attached = []            # local paths
        self._tip = 0
        # per-turn streaming state
        self.rows = []                 # rendered conversation rows
        self._think_buffer = ""
        self._think_hidden = False     # thinking bodies hidden by click
        self._answer_started = False
        self._render_scheduled = False

        self.setWindowTitle("Padauk — personal agent")
        self.resize(960, 700)
        self.setMinimumSize(680, 500)

        self._build_ui()

        QTimer.singleShot(50, self._poll)
        QTimer.singleShot(50, self._refresh_tools)
        threading.Thread(target=self._probe_bg, daemon=True).start()

    # ------------------------------------------------------------ UI build
    def _build_ui(self):
        central = QWidget()
        central.setStyleSheet(f"background:{BG};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header -----------------------------------------------------------
        head = QFrame()
        head.setStyleSheet(f"background:{PANEL};")
        head.setFixedHeight(52)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 6, 10, 6)
        hl.setSpacing(8)
        logo = QLabel("🕊 Padauk")
        logo.setStyleSheet(f"color:{TEXT}; font-size:17px; font-weight:700;")
        hl.addWidget(logo)
        self.status = QLabel("● starting…")
        self._set_status("● starting…", SUB)
        self.status.setCursor(Qt.PointingHandCursor)
        hl.addWidget(self.status)
        self.meta = QLabel("")
        self.meta.setStyleSheet(f"color:{SUB}; font-size:11px;")
        hl.addWidget(self.meta)
        hl.addStretch(1)
        hl.addWidget(self._btn("⚙ Status reset", self._recheck))
        hl.addWidget(self._btn("✨ New chat", self._new_chat))
        hl.addWidget(self._btn("💾 Save", self._save_chat))
        root.addWidget(head)

        # chat -------------------------------------------------------------
        self.chat = ChatBrowser()
        self.chat.anchorClicked.connect(
            lambda url: self._toggle_thinking() if "think" in url.path() else None)
        root.addWidget(self.chat, 1)
        self._greet()

        # attachments row --------------------------------------------------
        self.attach_row = QFrame()
        self.attach_row.setStyleSheet(f"background:{PANEL};")
        self.attach_la = QHBoxLayout(self.attach_row)
        self.attach_la.setContentsMargins(14, 4, 14, 0)
        self.attach_la.setSpacing(6)
        self.attach_row.hide()
        root.addWidget(self.attach_row)

        # composer ---------------------------------------------------------
        comp = QFrame()
        comp.setStyleSheet(f"background:{PANEL};")
        cl = QHBoxLayout(comp)
        cl.setContentsMargins(14, 8, 14, 6)
        cl.setSpacing(8)
        cl.addWidget(self._btn("📎 Attach", self._attach))
        cl.addWidget(self._btn("🔎 Web search", self._quick_search))

        self.send = QPushButton("Send ➤")
        self.send.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:#ffffff; border:none;"
            "border-radius:6px; padding:18px 20px; font-weight:700; }"
            f"QPushButton:disabled {{ background:{ACCENT_DIM}; color:#cdd3e0; }}"
            "QPushButton:pressed { background:#4272e0; }")
        self.send.setCursor(Qt.PointingHandCursor)
        self.send.clicked.connect(self._send)

        self.inp = InputEdit(self._send)
        self.inp.setFixedHeight(64)
        self.inp.setStyleSheet(
            f"QTextEdit {{ background:{PANEL_2}; color:{TEXT};"
            f" border:1px solid {LINE}; border-radius:6px; padding:8px 10px;"
            f" selection-background-color:{ACCENT_DIM}; selection-color:{TEXT}; }}")
        cl.addWidget(self.inp, 1)
        cl.addWidget(self.send)
        root.addWidget(comp)

        self.hint = QLabel(TIPS[0])
        self.hint.setStyleSheet(f"color:{SUB}; font-size:11px;"
                                "padding:0 18px 6px 18px; font-style:italic;")
        root.addWidget(self.hint)

    def _btn(self, label, cmd):
        b = QPushButton(label)
        b.setStyleSheet(
            f"QPushButton {{ background:{PANEL_2}; color:{TEXT}; border:none;"
            "border-radius:6px; font-size:12px; padding:7px 12px; }"
            f"QPushButton:hover {{ background:{LINE}; }}")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(cmd)
        return b

    def _greet(self):
        self.rows = [{"kind": "greet"}]
        self._render_now()

    # ------------------------------------------------------------- send
    def _send(self):
        if self.busy:
            return
        text = (self.inp.toPlainText() or "").strip()
        if not text:
            return
        self._attached = [p for p in self._attached if p in text]
        text = self._expand_command(text)
        self.inp.clear()
        self.history.append({"role": "user", "content": text})
        self._chat_turn_open()
        self._render_attached()
        self._waiting = False
        if self._probes and not self.tools_ok:
            # a probe may revive the tools; wait for it before going toolless
            self.history.pop()
            self.inp.setPlainText(text)
            self._waiting = True
            self._set_status("● waiting for the tool hub…", WARN)
            QTimer.singleShot(1000, self._retry_send)
            return
        self.busy = True
        self.send.setEnabled(False)
        self.send.setText("…")
        self._set_status("● contacting model…", SUB)
        threading.Thread(target=self._worker,
                         args=(list(self.history),), daemon=True).start()

    def _retry_send(self):
        if self.busy:
            return
        if self._waiting and self.history and self.history[-1]["role"] == "assistant":
            self._waiting = False
            return
        if self._waiting and not (self._probes and not self.tools_ok):
            self._waiting = False
            self._send()

    # -------------------------------------------------------- attachments
    def _attach(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a document for the agent to read",
            os.path.expanduser("~"),
            "Documents (*.pdf *.docx *.pptx *.xlsx *.doc *.txt *.md *.csv "
            "*.png *.jpg *.jpeg *.webp);;All files (*)")
        if not path:
            return
        cur = (self.inp.toPlainText() or "").strip()
        self.inp.moveCursor(QTextCursor.End)
        if not cur:
            self.inp.setPlainText(f"Summarize this document in Burmese. {path}")
        else:
            self.inp.insertPlainText(f" {path}")
        self.inp.moveCursor(QTextCursor.End)
        if path not in self._attached:
            self._attached.append(path)
        self._render_attached()
        self.inp.setFocus()

    def _render_attached(self):
        while self.attach_la.count():
            item = self.attach_la.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not self._attached:
            self.attach_row.hide()
            return
        self.attach_row.show()
        for p in self._attached:
            self.attach_la.addWidget(Chip(p, lambda _=False, pp=p: self._unattach(pp)))
        self.attach_la.addStretch(1)

    def _unattach(self, path):
        self._attached = [p for p in self._attached if p != path]
        text = (self.inp.toPlainText() or "").replace(path, "").strip()
        self.inp.clear()
        if text:
            self.inp.setPlainText(text)
        self._render_attached()
        self.inp.setFocus()

    def _quick_search(self):
        if not (self.inp.toPlainText() or "").strip():
            self.inp.setPlainText("/search ")
        else:
            self.inp.moveCursor(QTextCursor.End)
            self.inp.insertPlainText(" ")
        self.inp.setFocus()
        self.inp.moveCursor(QTextCursor.End)

    # ------------------------------------------------------------ worker
    def _worker(self, history):
        try:
            messages = [{"role": "system", "content": core.SYSTEM}] + history
            trace = []
            core.run_turn(messages, self.tools, stream=True, on_event=self._on_agent,
                          trace=trace)
            answer = messages[-1].get("content", "")
            if answer.strip():
                self.history.append({"role": "assistant", "content": answer})
            else:
                self.history.pop()
                self.q.put(("err", "The model returned an empty answer."))
        except Exception as exc:                          # noqa: BLE001
            try:
                self.history.pop()
            except IndexError:
                pass
            self.q.put(("err", str(exc)))
        self.q.put(("done",))

    def _on_agent(self, kind, *data):
        if kind == "thinking":
            self.q.put(("thinking", data[0]))
        elif kind == "answer":
            self.q.put(("answer", data[0]))
        elif kind == "notice":
            self.q.put(("tool", f"   ↻ {data[0]}"))
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
            self.q.put(("tool", f"{icon} {name}  {preview}"))
        elif kind == "tool_result":
            name, res = data
            body = res.get("result") if isinstance(res, dict) else None
            error = body.get("error") if isinstance(body, dict) else None
            if error:
                self.q.put(("tool", f"   ⚠ {name} failed: {error}"))
            elif isinstance(body, dict) and "results" in body:
                n = len(body.get("results") or [])
                cached = " · cached" if body.get("cached") else ""
                self.q.put(("tool", f"   {n} results{cached}"))

    # ------------------------------------------------- streaming (UI side)
    def _poll(self):
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "done":
                self._finish()
            elif kind == "err":
                if not self._answer_started:
                    self._flush_thinking_turn()
                self.rows.append({"kind": "err", "text": item[1]})
                self._set_status("● failed", ERR)
            elif kind == "thinking":
                self._think_buffer += item[1]
                self._set_status("● thinking…", SUB)
            elif kind == "answer":
                self._stream_answer(item[1])
            elif kind == "tool":
                self.rows.append({"kind": "tool", "text": item[1]})
            elif kind == "round":
                self._set_status(f"● thinking · round {item[1]}", SUB)
        self._render_soon()
        QTimer.singleShot(50, self._poll)

    def _stream_answer(self, chunk):
        if not self._answer_started:
            self._answer_started = True
            self._flush_thinking_turn()   # revealed once, above the answer
            self.rows.append({"kind": "answer", "text": chunk})
        else:
            self.rows[-1]["text"] += chunk
        self._set_status("● writing…", SUB)

    def _render_soon(self):
        """Coalesce re-renders: at most ~10 per second (streaming chunks are
        tiny; re-parsing the whole document per chunk would stutter)."""
        if getattr(self, "_render_scheduled", False):
            return
        self._render_scheduled = True
        QTimer.singleShot(100, self._render_now)

    def _render_now(self):
        self._render_scheduled = False
        self._render_rows()
        self._to_end()

    def _render_rows(self):
        """Rebuild the whole conversation as HTML (chat-UI standard: correct
        block styling every render, cheap to toggle thinking)."""
        parts = []
        body_hidden = self._think_hidden
        for row in self.rows:
            kind = row["kind"]
            if kind == "greet":
                parts.append(
                    f"<div style='color:{TEXT}; font-size:15px;"
                    f" font-weight:600; margin-top:4px;'>"
                    f"မင်္ဂလာပါ! ကျွန်မ Padauk ပါ။</div>"
                    f"<div style='color:{SUB}; margin-bottom:6px;'>"
                    f"– 📎 Attach a document, then Send<br>"
                    f"– 🔎 Web search: type a topic, press Enter<br>"
                    f"– Enter sends · Shift+Enter starts a new line</div>")
            elif kind == "user":
                text = esc(row["text"]).replace("\n", "<br>")
                parts.append(
                    f"<div style='background-color:{USER_BG}; color:#ffffff;"
                    f" margin:8px 16px 6px 150px; padding:8px 14px;"
                    f" border-radius:6px;'>{text}</div>")
            elif kind == "tool":
                text = esc(row["text"])
                parts.append(
                    f"<div style='color:{SUB}; font-size:11px;"
                    f" background-color:#191d29; margin:1px 150px 1px 16px;"
                    f" padding:1px 10px;'>{text}</div>")
            elif kind == "err":
                text = esc(row["text"])
                parts.append(
                    f"<div style='color:{ERR}; font-size:12px;"
                    f" background-color:#2a1c21; margin:6px 16px;"
                    f" padding:6px 12px;'>⚠ {text}</div>")
            elif kind == "thinking":
                text = esc(row["text"])
                label = THINK_HIDDEN_LABEL if body_hidden else THINK_LABEL
                body = "" if body_hidden else (
                    f"<div style='color:#7d879e; font-size:10px;"
                    f" background-color:#191d29; margin:0 150px 2px 16px;"
                    f" padding:2px 12px;'>{text}</div>")
                parts.append(
                    f"<div style='color:#8b96b3; font-size:11px;"
                    f" background-color:#191d29; margin:2px 150px 0 16px;"
                    f" padding:2px 12px;'>"
                    f"<a href='think' style='color:#8b96b3; font-size:11px;'>"
                    f"{label}</a></div>{body}")
            elif kind == "answer":
                text = esc(row["text"]).replace("\n", "<br>")
                parts.append(
                    f"<div style='background-color:{ASSIST_BG}; color:{TEXT};"
                    f" margin:6px 150px 8px 16px; padding:8px 14px;"
                    f" border-radius:6px;'>🕊 {text}</div>")
        self.chat.setHtml("".join(parts))

    def _toggle_thinking(self):
        self._think_hidden = not self._think_hidden
        self._render_now()

    def _chat_turn_open(self):
        text = self.history[-1]["content"]
        self.rows.append({"kind": "user", "text": text})
        # reset per-turn state
        self._answer_started = False
        self._think_buffer = ""
        self._render_now()

    def _flush_thinking_turn(self):
        """Move gathered thinking into the row list (called when the answer
        starts, or on finish when the turn produced no answer)."""
        buf = self._think_buffer.strip()
        self._think_buffer = ""
        if not buf:
            return
        if len(buf) > THINK_CHARS:
            buf = buf[:THINK_CHARS] + " …"
        # insert above the answer row if one exists
        index = 0
        for i, row in enumerate(self.rows):
            if row["kind"] == "answer":
                break
            index = i + 1
        self.rows.insert(index, {"kind": "thinking", "text": buf})

    def _to_end(self):
        sb = self.chat.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------------ status
    def _set_status(self, text, color):
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{color}; font-size:12px;")

    def _finish(self):
        self.busy = False
        if not self._answer_started:
            self._answer_started = True   # a turn that only thought still shows it
            self._flush_thinking_turn()
        self._render_now()
        self.send.setEnabled(not self.hub_down)
        self.send.setText("Send ➤")
        self.inp.setFocus()
        threading.Thread(target=self._probe_bg, daemon=True).start()

    def _refresh_tools(self):
        def load():
            tools = core.load_tool_schemas()
            core.AVAILABLE = {t["name"] for t in tools}
            self.tools = tools
            self.q.put(("hub", bool(tools)))
            self.q.put(("tok", "tools loaded" if tools else "tools: hub offline"))
        threading.Thread(target=load, daemon=True).start()

    def _probe_bg(self):
        """Probe in a thread; _send waits while a probe is in flight."""
        with self._probe_lock:
            self._probes += 1
        try:
            self._probe_once()
        finally:
            with self._probe_lock:
                self._probes -= 1

    def _probe_once(self):
        hub = model = False
        try:
            urllib.request.urlopen(f"{core.HUB_URL}/health", timeout=3).read()
            hub = True
        except Exception:                                 # noqa: BLE001
            pass
        try:
            body = json.dumps({"model": core.MODEL}).encode()
            req = urllib.request.Request(
                f"{core.OLLAMA_URL}/api/show", data=body,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
            model = True
        except Exception:                                 # noqa: BLE001
            pass
        self.tools_ok = hub and model
        if hub and model:
            self.q.put(("status", ("● ready", OK)))
        elif model:
            self.q.put(("status", ("● model ok · tools down", WARN)))
        elif hub:
            self.q.put(("status", ("● tools ok · model down", WARN)))
        else:
            self.q.put(("status", ("● offline", ERR)))
        if not hub:
            self._refresh_tools()      # schemas may load now that the hub is up

    def _recheck(self):
        self._set_status("● checking…", SUB)
        threading.Thread(target=self._probe_bg, daemon=True).start()
        self._refresh_tools()

    def _tick(self):
        if not self.busy:
            threading.Thread(target=self._probe_bg, daemon=True).start()
        self._tip = (self._tip + 1) % len(TIPS)
        self.hint.setText(TIPS[self._tip])

    # ------------------------------------------------------------ misc
    def _new_chat(self):
        self.history.clear()
        self._attached.clear()
        self._render_attached()
        self._greet()
        self.inp.clear()
        self._recheck()

    def _save_chat(self):
        if not self.history:
            self.rows.append({"kind": "err", "text": "Nothing to save yet."})
            self._render_now()
            return
        default = datetime.datetime.now().strftime("padauk-chat-%Y%m%d-%H%M.md")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save chat", os.path.join(BASE, default), "Markdown (*.md)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            for m in self.history:
                role = {"user": "🧑 You", "assistant": "🕊 Padauk"
                        }.get(m["role"], m["role"])
                f.write(f"**{role}:**\n\n{m.get('content', '')}\n\n---\n\n")

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


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PadaukChat")
    app.setStyle("Fusion")
    # Layer 3 of the font defense: an app-wide Myanmar-capable family, applied
    # through QFont (not stylesheets), so every widget inherits it.
    app.setFont(QFont(resolve_myanmar_family(), 11))
    app.setStyleSheet(
        "QMainWindow, QWidget { background:%s; }"
        "QLabel { color:%s; }"
        "QToolTip { background:%s; color:%s; border:1px solid %s; }"
        % (BG, TEXT, PANEL_2, TEXT, LINE))
    win = App()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
