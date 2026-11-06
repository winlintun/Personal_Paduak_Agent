# Personal Padauk Agent — README (မြန်မာလို)

သင့်ကွန်ပျူတာအတွင်းမှာသာ အလုပ်လုပ်သော မြန်မာဘာသာ AI agent ဖြစ်သည်။
**Padauk model (Ollama)** + **Hirara tool hub (Docker)** ကို ပေါင်းစပ်ထားသည်။

> 🔒 **ပုဂ္ဂလိကမှု (Privacy):** အားလုံးသည် သင့်စက်အတွင်းမှာသာ လည်ပတ်သည် (local-only)။

---

## 📁 ဖိုင်တည်ဆောက်ပုံ (Project Layout)

```text
personal_paduak_agent/
├── Hirara/                     tool toolkit (Docker ဖြင့် လည်ပတ်သည်)
│   └── docker-compose.slim.yml   tool များ စတင်/ရပ်ရန် file
├── PadaukAgent.py              agent အဓိက script (Ollama ↔ tools)
├── PadaukChatQt.py             စကားပြေ window အဓိက GUI (Qt — မြန်မာစာ မှန်ကန်စွာ ပေါ်သည်)
├── PadaukChat.py               အရေးပေါ် အစားထိုး GUI (Tk — မြန်မာစာ မမှန်ကန်ပါ)
├── fonts/                      မြန်မာ font များ (Noto Sans Myanmar, Pyidaungsu)
├── Padauk-Agent-README.md      အင်္ဂလိပ်လို quick start guide
└── docs-test.md                စမ်းသပ်ရန် နမူနာ စာတမ်း
```

---

## ✅ လိုအပ်ချက်များ (Requirements)

| အရာ | ရှင်းလင်းချက် |
|---|---|
| Ollama | မော်ဒယ် လည်ပတ်ရန် — <https://ollama.com> မှ ထည့်ပါ |
| Docker | Hirara tool များ လည်ပတ်ရန် |
| Python 3 | system Python 3.12 ဖြင့် စမ်းသပ်ထားသည် |
| PySide6 | Qt GUI အတွက် — `pip install --user --break-system-packages PySide6` |
| မှတ်ဉာဏ် | မော်ဒယ် အရွယ်အစား ~8 GB ဖြစ်သဖြင့် RAM လုံလောက်ရန် လိုအပ်သည် |

---

## 🛠 တစ်ကြိမ်တည်း စီစင်ခြင်း (One-time Setup)

### ၁) မော်ဒယ်ကို download လုပ်ပြီး Ollama ထဲသို့ ထည့်ပါ

မော်ဒယ်ဖိုင် (~8 GB) သည် GitHub repo ထဲတွင် **မပါပါ**။ Hugging Face မှ ရယူပါ:

**Model:** <https://huggingface.co/WYNN747/ai4burmese-padauk-gguf>

```bash
# ၁) GGUF (~7 GB) နှင့် Modelfile ကို download လုပ်ပါ
curl -L -o padauk-gemma-q8_0.gguf \
  "https://huggingface.co/WYNN747/ai4burmese-padauk-gguf/resolve/main/padauk-gemma-q8_0.gguf"
curl -L -o Modelfile \
  "https://huggingface.co/WYNN747/ai4burmese-padauk-gguf/resolve/main/Modelfile"

# ၂) Modelfile ကို ပြင်ပါ — ပုံစံအတိုင်း ချန်ထားပါက အဖြေအလွတ် ဖြစ်နိုင်သည်
sed -i 's/num_predict 512/num_predict 4096/' Modelfile

# ၃) Ollama ထဲသို့ အမည် "padauk-gemma" ဖြင့် ထည့်သွင်းပါ
ollama create padauk-gemma -f Modelfile

# (မဖြစ်မနေ မဟုတ်ပါ) ပြီးလျှင် GGUF ဖိုင်ကြီးကို ဖျက်နိုင်သည်
rm padauk-gemma-q8_0.gguf
```

အတည်ပြုရန်: `ollama list` တွင် `padauk-gemma:latest` ပေါ်ရမည်။

> ⚠️ **မှတ်ချက်:** `q8_0` မော်ဒယ်ကို **မသုံးပါနှင့်** — `num_predict 512` ကြောင့်
> အဖြေ **အလွတ် (empty answer)** ဖြစ်တတ်သည်။ `padauk-gemma:latest` ကိုသာ သုံးပါ။

### ၂) Tool များကို စတင်ပါ (reboot လုပ်ပြီးတိုင်း ပြန်လုပ်ရန်)

```bash
cd ~/Desktop/personal_paduak_agent/Hirara
sg docker -c "docker compose -f docker-compose.slim.yml up -d"
```

အတည်ပြုရန်:

```bash
curl localhost:8080/health      # "status": "ok" ပေါ်ရမည်
curl localhost:8080/schemas     # ရနိုင်သော tool များ စာရင်း
```

> 💡 Docker command အားလုံးကို `sg docker -c "…"` ဖြင့် ရစ်ပါ။
> တိုက်ရိုက် `docker` လို့ရေးပါက **permission denied** ဖြစ်နိုင်သည်။

---

## 🚀 အသုံးပြုပုံ (How to Use)

```bash
# စာတမ်း ဖတ်ခိုင်းခြင်း (txt / md / pdf / docx / pptx / xlsx / scanned pdf)
python3 ~/Desktop/personal_paduak_agent/PadaukAgent.py \
  "Read ~/Documents/report.pdf and summarize in Burmese"

# အင်တာနက် ရှာဖွေခြင်း
python3 ~/Desktop/personal_paduak_agent/PadaukAgent.py \
  "Search the web: today's currency rate USD to MMK"

# terminal တွင် တိုက်ရိုက် စကားပြေခြင်း
python3 ~/Desktop/personal_paduak_agent/PadaukAgent.py

# စကားပြေ window (အဓိက GUI — Qt)
python3 ~/Desktop/personal_paduak_agent/PadaukChatQt.py

# အရေးပေါ် အစားထိုး GUI (Tk — မြန်မာစာ ဖောက်ပြန်နိုင်သည်)
python3 ~/Desktop/personal_paduak_agent/PadaukChat.py
```

Desktop တွင် double-click launcher လည်း ရှိသည်: `~/Desktop/PadaukChat.desktop`

### 🖼 စကားပြေ window တွင် အသုံးပြုနိုင်သည့် အရာများ

| လုပ်ဆောင်ချက် | ပုံစံ |
|---|---|
| ပေးပို့ | **Enter** (စာကြောင်းအသစ် = **Shift+Enter**) |
| စာတမ်း ထည့်ရန် | **📎 Attach** (ဖယ်ရှားပြန်ရန်လည်း ရသည်) |
| Web search | **🔎 Web search** ခလုတ် |
| အဖြေရှည် ရပ်ရန် | **■ Stop** သို့မဟုတ် **Esc** (ရေးပြီးသား အဖြေ မပျောက်ပါ) |
| စာကူးရန် | message ဘေးရှိ **⧉ copy** |
| နောက်ဆုံး message သို့ | **↓ latest** (အပေါ် scroll တင်လိုက်လျှင် ပေါ်လာသည်) |
| စကားပြေမှု အသစ် | **Ctrl+N** ၊ သိမ်းရန် **Ctrl+S** |
| Think ကြံစိတ်ကြည့်ရန် | အဖြေအပေါ်တွင် ဖော်ပြထားသော အရောင် **💭 thinking** block ကို နှိပ်ကြည့်ပါ |

Slash command များ:

```text
/read-file <path> [question]   → ဖိုင်တစ်ခု ဖတ်ခိုင်းရန်
/search <query>                → web ရှာဖွေရန်
```

### 📄 ဖတ်နိုင်သော ဖိုင်အမျိုးအစားများ

- `txt`, `md`, `csv` နှင့် အခြား plain text
- `pdf` (ရိုက်ကူး စာလုံးမပါ)၊ `docx`, `pptx`, `xlsx`
- Scanned pdf နှင့် ပုံများ (OCR ဖြင့်)

> ⚠️ မြောက်ခေတ် **`.doc`, `.xls`, `.ppt`** ကို **မထောက်ပံ့ပါ**။
> အရင်ဆုံး LibreOffice/Word ဖြင့် ဖွင့်ပြီး `.docx`/`.xlsx`/`.pptx` အဖြစ်
> save လုပ်ပါ။

---

## ❓ မကြာခဏ တွေ့ရသော ပြဿနာများ (Troubleshooting)

| ပြဿနာ | ဖြေရှင်းပုံ |
|---|---|
| အဖြေ **အလွတ်** သာ ပေါ်သည် | `padauk-gemma-q8_0` ကို သုံးနေခြင်း ဖြစ်နိုင်သည်။ `ollama list` ဖြင့် စစ်ပြီး `padauk-gemma:latest` သို့ ပြောင်းပါ။ Modelfile တွင် `num_predict 4096` ရှိရမည်။ |
| `/health` ချိတ်ဆက် မရပါ | Docker tool များ မစတင်ရသေးပါ။ အပေါ်ပိုင်း "Tool များကို စတင်ပါ" အပိုင်းကို ပြုလုပ်ပါ။ |
| `docker` command မလုပ်နိုင် | `sg docker -c "…"` ပုံစံဖြင့် ပြန်စမ်းပါ။ |
| မြန်မာစာ | **Qt GUI** (`PadaukChatQt.py`) ကို သုံးပါ — Tk fallback မှာ မမှန်ကန်ပါ။ |
| အဖြေ ရှည်လျားနေသည် | **■ Stop** သို့မဟုတ် **Esc** နှိပ်ပါ — ရေးပြီးသား အပိုင်း ကျန်ထားပေးသည်။ |
| `file` / `browser` tool များ မပါ | GHCR image pull ကို ကန့်သတ်ခံရခြင်း ဖြစ်နိုင်သည်။ `docker pull ghcr.io/lucasdmarshall/hirara-fs:latest` သို့ နောက်တစ်မျိုး ပြန်ကြိုးစားပါ။ |

---

## 🔐 လုံခြုံမှု မှတ်ချက်

- Hirara hub သည် **`127.0.0.1` (loopback) local**   အတွင်းသာ run သည် — **(never publish the port)**။
- `searxng/settings.yml` ထဲတွင် generate လုပ်ထားသော secret key ပါဝင်သည် —
  **မထုတ်ဝေပါနှင့်**။

---

## 💬 မေးခွန်း ရှိပါသလား?

အင်္ဂလိပ်လို အသေးစိတ် guide ကို `Padauk-Agent-README.md` တွင် ဖတ်နိုင်သည်။
Agent ဖြင့် စမ်းသပ်ကြည့်လိုလျှင်:

```bash
ollama list                                     # မော်ဒယ် ရှိမရှိ
curl localhost:8080/health                      # tool hub အသုံးပြုနိုင်မရှိ
python3 ~/Desktop/personal_paduak_agent/PadaukChatQt.py   # စကားပြေ window ဖွင့်ခြင်း
```

စတင်သုံးရန် စမ်းသပ်လိုလျှင် chat window တွင် "Read docs-test.md and summarize" လို့
ရေးပြီး စမ်းသပ်ကြည့်နိုင်သည် 🙂
