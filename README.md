# Homework Checker AI

An AI teaching assistant that grades university-level homework across
many subjects and language skills (PDF, image, Word doc, or audio for
Speaking — typed or handwritten) question-by-question, using Gemini as
the grading engine. Produces HTML, PDF, and Excel reports, and is
usable both from a Streamlit teacher dashboard and from a REST API for
integrating with other apps/websites.

```
Upload Homework  (Streamlit UI  --or--  REST API call from another app)
      ↓
Python Backend
      ↓
Gemini API  (system prompt built per subject / language+skill)
      ↓
Homework Analyzer  →  JSON Response
      ↓
Generate: HTML report · PDF report · Excel gradebook
      ↓
Teacher Dashboard  --or--  JSON/file returned to the calling app
```

## Project structure

```
HomeworkCheckerAI/
├── app.py                 # Streamlit UI (upload, review, export)
├── api.py                  # FastAPI REST API (for other apps/websites)
├── config.py                # Paths, model list, backend switch, grading scale
├── prompts.py                 # Subject/language-skill system prompts
├── homework_checker.py         # Gemini backend + get_checker() factory
├── local_checker.py             # Self-hosted (vLLM/open-model) backend
├── training_logger.py            # Logs data for future fine-tuning
├── jobs.py                        # Redis/RQ queue for POST /jobs (async grading)
├── worker_tasks.py                 # The grading task run by `rq worker`
├── report_generator.py            # HTML / PDF / Excel report builders
├── utils.py                        # Upload validation, docx text extraction
├── uploads/                         # Saved student submissions (gitignored)
├── reports/                          # Generated reports (gitignored)
├── training_data/                     # Fine-tuning dataset (gitignored)
│   ├── inputs/                          # Copies of graded input files
│   ├── raw_log.jsonl                     # Every graded submission (unverified)
│   └── gold.jsonl                         # Teacher-verified examples
├── templates/
│   └── report.html                        # Jinja2 HTML report template
├── static/
│   └── fonts/                              # Bundled Unicode font for PDF reports
├── requirements.txt
└── .env.example
```


## Setup

Commands below are grouped by OS where they differ. Everything else
(the Python code itself, requirements.txt, the app's behavior) is
identical across all three — only how you create a venv, activate it,
and set environment variables differs.

### 1. Install Python 3.10+

- **Windows**: download from [python.org](https://www.python.org/downloads/) (check "Add python.exe to PATH" during install), or `winget install Python.Python.3.12`
- **macOS**: `brew install python@3.12` (or use the python.org installer)
- **Linux**: usually preinstalled; if not, `sudo apt install python3 python3-venv python3-pip` (Debian/Ubuntu)

### 2. Create and activate a virtual environment, install dependencies

**Windows (PowerShell):**
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
> If `Activate.ps1` is blocked by execution policy, run PowerShell as
> Administrator once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**Windows (cmd.exe):**
```cmd
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt
```

**macOS / Linux (bash/zsh):**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Get a Gemini API key

From Google AI Studio: https://aistudio.google.com/apikey

### 4. Set your API key

Either paste it directly into the sidebar text box when the app is
running (simplest for quickly trying things out), or set it as an
environment variable so you don't have to re-enter it each time:

**Windows (PowerShell):**
```powershell
$env:GEMINI_API_KEY="your-key-here"
```
This only lasts for the current terminal session. To set it
permanently: `setx GEMINI_API_KEY "your-key-here"` (then open a new terminal).

**Windows (cmd.exe):**
```cmd
set GEMINI_API_KEY=your-key-here
```

**macOS / Linux (bash/zsh):**
```bash
export GEMINI_API_KEY=your-key-here
```
To make this permanent, add that line to `~/.zshrc` (macOS default
shell) or `~/.bashrc` (Linux/bash), then restart your terminal.

**Recommended for all OSes: use a `.env` file instead.** It's loaded
automatically (via `python-dotenv`, already in requirements.txt) every
time the app starts, so you never need to re-run export/set/`$env:`
commands each session — and it's the same one file regardless of OS:
```bash
cp .env.example .env    # Windows: copy .env.example .env
# then edit .env and paste your key
```

### 5. Run the Streamlit app (teacher dashboard)

Same command on every OS, once the venv is activated:
```bash
streamlit run app.py
```
Streamlit will open the app in your browser (default: http://localhost:8501).

### 6. Run the REST API (optional — for other apps/websites to call)

```bash
uvicorn api:app --reload --port 8000
```
Interactive docs (Swagger UI) then live at http://localhost:8000/docs
— you can try every endpoint directly from the browser there.

## REST API (api.py)

Other apps/websites submit homework here instead of through the
Streamlit UI. It returns the exact same JSON schema the dashboard shows.

### Endpoints

| Method | Path                 | Purpose                                              |
|--------|----------------------|-------------------------------------------------------|
| GET    | `/health`             | Health check                                          |
| GET    | `/backend`             | Current MODEL_BACKEND + training-data stats           |
| GET    | `/subjects`            | List standard subject keys/labels                     |
| GET    | `/languages`            | List language keys/labels                             |
| GET    | `/skills`                | List language-skill keys/labels                       |
| POST   | `/analyze`                 | Grade one file **synchronously** (blocks until done) |
| POST   | `/jobs`                      | **Enqueue** one file for grading, returns instantly  |
| GET    | `/jobs/{job_id}`               | Poll a job's status/result                          |
| POST   | `/reports/html`              | Turn an `/analyze`-shaped response into an HTML file  |
| POST   | `/reports/pdf`                 | Turn an `/analyze`-shaped response into a PDF file  |
| POST   | `/reports/gradebook`             | Combine several such responses into one .xlsx     |

**Use `/analyze` for testing or low-volume use. Use `/jobs` + `/jobs/{id}`
for your CRM integration** — see "Handling many simultaneous submissions"
below for why, and for setup instructions.

### Grading a standard subject

```bash
curl -X POST http://localhost:8000/analyze \
  -F "file=@homework.pdf" \
  -F "subject_key=math" \
  -F "student_name=Ali" \
  -F "api_key=YOUR_GEMINI_API_KEY"
```

### Grading a language skill (e.g. English Speaking)

```bash
curl -X POST http://localhost:8000/analyze \
  -F "file=@recording.mp3" \
  -F "language_key=english" \
  -F "skill_key=speaking" \
  -F "student_name=Ali" \
  -F "api_key=YOUR_GEMINI_API_KEY"
```

### Turning a result into a downloadable report

```bash
# save the /analyze response to result.json first, then:
curl -X POST http://localhost:8000/reports/pdf \
  -H "Content-Type: application/json" \
  -d @result.json \
  -o report.pdf
```

## Handling many simultaneous submissions (CRM integration)

If your CRM can send many homework submissions at roughly the same time
(e.g. a whole class submitting near a deadline), calling `/analyze`
directly for each one has real limits: each call blocks until Gemini
responds (often 10-30+ seconds), so many at once can queue up behind
each other, risk your CRM's own HTTP timeout, and can trigger Gemini's
rate limits if too many fire in the same moment.

**`/jobs` solves this with a queue.** It returns a `job_id` in
milliseconds — grading happens afterward, in a separate background
worker process, so the API itself never blocks no matter how many
submissions land at once.

### Setup (one-time)

1. **Install and run Redis** (the queue's backing store):

   **macOS:**
   ```bash
   brew install redis && brew services start redis
   ```

   **Linux:**
   ```bash
   sudo apt install redis-server && sudo systemctl start redis-server
   ```

   **Windows:** Redis itself isn't officially supported/maintained for
   Windows. Three practical options, easiest first:
   - **Docker** (recommended if you have Docker Desktop):
     ```powershell
     docker run -d --name redis -p 6379:6379 redis:latest
     ```
   - **WSL2** (Windows Subsystem for Linux) — install Ubuntu from the
     Microsoft Store, then inside the WSL terminal run the Linux
     commands above (`sudo apt install redis-server ...`). The API and
     workers can run on Windows and still reach Redis in WSL at
     `localhost:6379` (WSL2 shares localhost with Windows by default).
   - **[Memurai](https://www.memurai.com/)** — a native Windows-compatible
     Redis alternative with a free tier, if you'd rather avoid Docker/WSL.

   Whichever option you use, confirm it's reachable:
   ```bash
   redis-cli ping   # should print: PONG
   ```

2. **Start one or more workers** (separate terminal/process from the API):
   ```bash
   rq worker homework_grading
   ```
   Run this command multiple times (in separate terminals, or on
   separate machines, all pointed at the same `REDIS_URL`) to process
   several submissions in parallel — each worker instance handles one
   job at a time, so N workers = N submissions graded simultaneously.

   > **Windows note:** `rq worker`'s default process-forking mode relies
   > on Unix-only APIs and does not work on native Windows. Either run
   > workers inside WSL2 (recommended — same environment as Linux), or
   > on native Windows add `--worker-class rq.worker.SimpleWorker` to
   > the command above, which avoids forking at the cost of slightly
   > less isolation between jobs.

3. **Start the API as usual:**
   ```bash
   uvicorn api:app --port 8000
   ```

### Usage

```bash
# 1. Submit -- returns instantly:
curl -X POST http://localhost:8000/jobs \
  -F "file=@homework.pdf" -F "subject_key=math" \
  -F "student_name=Ali" -F "api_key=YOUR_GEMINI_API_KEY"
# -> {"job_id": "27bbae43-...", "status": "queued"}

# 2. Poll until done (your CRM does this on a timer, e.g. every few seconds):
curl http://localhost:8000/jobs/27bbae43-...
# while working:  {"job_id": "...", "status": "started"}
# when done:      {"job_id": "...", "status": "finished", "result": { ...same JSON /analyze returns... }}
# on failure:     {"job_id": "...", "status": "failed", "error": "..."}
```

### Running this in production

- Keep the worker(s) running continuously with a process manager —
  `systemd`, `supervisor`, or a Docker container with `restart: always` —
  so a crashed worker restarts automatically instead of silently
  stopping job processing.
- Scale throughput by running more `rq worker homework_grading`
  processes, not by adding more API instances — the API's job is just
  to enqueue quickly, which it already does.
- `JOB_TIMEOUT_SECONDS` (config.py, default 300) caps how long a single
  job can run before RQ marks it failed — raise it if you expect very
  large files or a slow local model.
- `JOB_RESULT_TTL_SECONDS` (default 24 hours) controls how long a
  finished job's result stays fetchable via `/jobs/{id}` before Redis
  expires it — make sure your CRM polls (or fetches) well within that
  window.

### Protecting the API (recommended before deploying publicly)

Set `INTERNAL_API_KEY` on the server:

```bash
# Windows (PowerShell):
$env:INTERNAL_API_KEY="some-long-random-string"

# Windows (cmd.exe):
set INTERNAL_API_KEY=some-long-random-string

# macOS / Linux:
export INTERNAL_API_KEY="some-long-random-string"
```

(In production, on any OS, this is usually set in `.env` or your process
manager's environment config rather than typed into a terminal each time.)

Every caller must then include it as a header:

```bash
curl -X POST http://localhost:8000/analyze \
  -H "X-Internal-Api-Key: some-long-random-string" \
  -F "file=@homework.pdf" -F "subject_key=math" -F "api_key=YOUR_GEMINI_API_KEY"
```

This stops random internet traffic from burning through your Gemini
quota/billing once the API is reachable from outside your machine. It's
unrelated to `GEMINI_API_KEY`/the `api_key` field — that one authenticates
*to Gemini*; `INTERNAL_API_KEY` authenticates *callers of your API*.

Also tighten `allow_origins=["*"]` in `api.py`'s CORS middleware to your
actual frontend domain(s) before deploying publicly.

## How it works

- **Sidebar → Assessment type**: choose **Standard subject** (Math, Physics,
  Chemistry, Biology, Computer Science, Essay/Literature, History, or a
  custom "General / Other" subject you type in) or **Language skill**
  (English / Russian / Turkish × Writing / Reading / Listening / Speaking).
  This swaps in a tailored grading system prompt — different roles,
  objectives, error categories, and score rubrics — while every mode still
  returns the same JSON schema, so reports work identically either way.
- **PDF / image / DOCX uploads** cover Math-type subjects and the Writing/
  Reading/Listening language skills (typed or handwritten work, completed
  worksheets, essays, etc).
- **Audio uploads** (.mp3 / .wav / .m4a / .ogg) are for the **Speaking**
  skill — upload a recording of the student talking, and Gemini listens to
  it directly to assess pronunciation, fluency, grammar, and vocabulary.
- **PDF / image uploads** are sent directly to Gemini as multimodal input —
  the model reads typeset or handwritten math (or text) straight off the
  page, no OCR pipeline needed.
- **DOCX uploads** are text-extracted locally with `python-docx` first
  (Gemini has no native Word-doc reader), then sent as text.
- Gemini is given a strict system prompt (built dynamically in
  `prompts.py`) instructing it to behave like an expert examiner for the
  chosen subject/skill, grade every detected question/task 0–100,
  categorize mistakes, and return **only** JSON in a fixed schema.
- `utils.parse_and_validate_json` strips any accidental markdown fences
  and checks the required keys are present; `homework_checker.py`
  automatically asks the model to self-correct once or twice if the
  JSON is malformed.
- `report_generator.py` turns that JSON into:
  - a styled **HTML** report (`templates/report.html`, via Jinja2)
  - a printable **PDF** report (via `fpdf2` with a bundled Unicode font,
    so math symbols like √, ∫, π render correctly — no system
    dependencies like wkhtmltopdf/weasyprint required)
  - a combined **Excel gradebook** (via `openpyxl`) across every
    submission graded in the current session, with an overview sheet
    and a per-question detail sheet.

## Customizing the grading rubric

- Standard subjects live in `prompts.py` as `SubjectProfile` entries
  (`MATH`, `PHYSICS`, `CHEMISTRY`, `BIOLOGY`, `COMPUTER_SCIENCE`, `ESSAY`,
  `HISTORY`, `GENERAL`). Edit objectives/error_categories/score_rubric on
  any of them, or add a new one and register it in the `SUBJECTS` dict.
- Languages live in `LANGUAGES` (add a 4th language by adding one line).
  Skills (Writing/Reading/Listening/Speaking) live in `SKILL_TEMPLATES` —
  edit objectives/error_categories/score_rubric there to change how any
  skill is graded across all languages at once.
- Keep the `OUTPUT FORMAT` JSON schema (`_OUTPUT_FORMAT_BLOCK`) unchanged
  unless you also update `utils.parse_and_validate_json` and
  `report_generator.py` to match, since they depend on those exact keys
  (`overall_score`, `grade`, `questions[]`, `summary`).

## Notes / next steps

- Swap `gemini-2.5-flash` for `gemini-2.5-pro` in the sidebar for harder
  material (e.g. graduate-level proofs) where reasoning quality matters
  more than speed.
- `uploads/` and `reports/` are working directories — wire them up to
  cloud storage (S3, GCS) if you deploy this for multiple teachers.
- For very long, multi-page assignments, consider splitting into one
  Gemini call per page/question to stay well under output token limits.

## Phase 2: running your own model instead of Gemini

This project supports a full switch away from Gemini to a self-hosted,
open-weight model — for teams that want zero dependency on any external
AI company. This is a bigger undertaking than everything above, so read
this section fully before starting.

### What actually changes, and what doesn't

Only **one setting** decides which backend grades homework:

```bash
export MODEL_BACKEND=local   # or "gemini" (default)
```

`app.py`, `api.py`, `report_generator.py`, and the JSON schema never
change. `homework_checker.get_checker()` is the single place that reads
`MODEL_BACKEND` and hands back either the Gemini-backed `HomeworkChecker`
or the self-hosted `LocalModelChecker` (`local_checker.py`) — both expose
the exact same `.analyze(file_path, extra_instructions)` interface.

### Architecture

| Task | Local model |
|---|---|
| Text + vision (Math, Physics, Essay, Reading, Writing, etc.) | **Qwen2.5-VL** (32B or 72B) — reads PDFs/images/handwriting |
| Audio (Speaking) | **Qwen2-Audio** or **Qwen2.5-Omni** — listens to the recording directly |
| Serving | **vLLM**, exposing an OpenAI-compatible REST API on your GPU server |

One real difference from Gemini: open vision-language models take
**images**, not raw PDF bytes. `local_checker.py` converts PDFs to page
images automatically with PyMuPDF before sending them — this is
invisible to the rest of the app, but it's worth knowing about.

### Three different paths depending on your hardware and OS

- **NVIDIA GPU server, native Linux (A100/H100/etc.)** → use **vLLM**
  (Step 1 below). This is the production path: highest throughput,
  supports serving multiple models, LoRA adapters, many concurrent users.
- **NVIDIA GPU on Windows** → vLLM doesn't run on native Windows either
  (it needs a Linux CUDA environment). Install **WSL2** with Ubuntu
  (`wsl --install` in an admin PowerShell, then set up NVIDIA's
  [CUDA-on-WSL drivers](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)),
  and follow the Linux/vLLM instructions (Step 1 below) *inside* WSL2 —
  it gets near-native GPU performance this way. Alternatively, Ollama
  (below) has a native Windows installer and is far simpler to set up,
  at the cost of vLLM's production throughput/scaling features.
- **Apple Silicon Mac (M1/M2/M3, unified memory)** → vLLM does not run
  here at all (no NVIDIA CUDA on macOS, ever). Use **Ollama** instead —
  a genuinely different tool, not a smaller version of vLLM.

Ollama (whether on macOS, Windows, or Linux) is the right choice for
learning/prototyping on a single machine, not for serving many students
at once — see the honest limits note below.

#### Quick start with Ollama — for learning/prototyping only

**1. Install Ollama:**
```bash
# macOS:
brew install ollama

# Windows: download and run the installer from https://ollama.com/download/windows
# (installs as a background service; no separate "ollama serve" step needed)

# Linux:
curl -fsSL https://ollama.com/install.sh | sh
```

**2. Start it** (macOS/Linux only — Windows runs it as a service automatically):
```bash
ollama serve
```

**3. In another terminal, pull a vision-language model** (same command, every OS):
```bash
# Start small to confirm everything works:
ollama pull qwen2.5vl:7b
# Once that works, try a stronger one if your RAM/VRAM comfortably allows it:
ollama pull qwen2.5vl:32b
```

**4. Quick sanity check outside the app:**
```bash
ollama run qwen2.5vl:7b "Say hello"
```

**5. Point the app at it:**
```bash
# Windows (PowerShell):
$env:MODEL_BACKEND="local"
$env:LOCAL_MODEL_BASE_URL="http://localhost:11434/v1"
$env:LOCAL_MODEL_API_KEY="ollama"
$env:LOCAL_TEXT_MODEL="qwen2.5vl:7b"

# macOS / Linux:
export MODEL_BACKEND=local
export LOCAL_MODEL_BASE_URL="http://localhost:11434/v1"
export LOCAL_MODEL_API_KEY="ollama"          # Ollama ignores the value, but the field can't be empty
export LOCAL_TEXT_MODEL="qwen2.5vl:7b"       # or qwen2.5vl:32b once confirmed working
```
```bash
pip install openai pymupdf
streamlit run app.py
```

Speaking will keep using Gemini automatically (see `FORCE_GEMINI_FOR_SPEAKING`
in config.py) — mature, easy-to-run local audio models aren't really
available for consumer hardware yet, so this avoids a dead end there.

**Honest limits of this path**: a laptop or desktop is fine for trying
things out and building intuition, but it is not a real "server" — it
has far less raw compute than an A100/H100, needs to stay awake and on
the same network as whoever's using the app, and can only handle one or
a few requests at a time before things slow down noticeably. Treat this
as how you'd learn and prototype the workflow, not as infrastructure for
real students at scale. When you're ready for that, the vLLM path below
(on a rented or owned NVIDIA GPU server, or WSL2 on a Windows GPU
machine) is the one to use — everything else about the app (config,
`local_checker.py`, the rest of this guide) stays identical, you're only
swapping which server `LOCAL_MODEL_BASE_URL` points to.

### Step 1 — Serve a model with vLLM on your GPU server

On your A100/H100 server:

```bash
pip install vllm

# Vision-language model, for everything except Speaking:
vllm serve Qwen/Qwen2.5-VL-32B-Instruct \
  --port 8000 \
  --tensor-parallel-size 2 \
  --max-model-len 32768

# Separately, for Speaking (different port, can run alongside):
vllm serve Qwen/Qwen2-Audio-7B-Instruct \
  --port 8001 \
  --tensor-parallel-size 1
```

Adjust `--tensor-parallel-size` to however many GPUs you're splitting
the model across, and `--max-model-len` to your context needs (homework
pages + system prompt is usually a few thousand tokens; leave headroom).
72B-class models need more GPUs/VRAM than 32B — start with 32B, confirm
quality, then scale up if needed.


### Step 2 — Point the app at your server

This is run on whichever machine hosts the Streamlit app / API (your
own laptop or desktop, not the GPU server itself), since that's where
`streamlit run app.py` / `uvicorn api:app` actually execute:

```bash
# Windows (PowerShell):
$env:MODEL_BACKEND="local"
$env:LOCAL_MODEL_BASE_URL="http://YOUR_SERVER_IP:8000/v1"
$env:LOCAL_TEXT_MODEL="Qwen/Qwen2.5-VL-32B-Instruct"
$env:LOCAL_AUDIO_MODEL="Qwen/Qwen2-Audio-7B-Instruct"

# macOS / Linux:
export MODEL_BACKEND=local
export LOCAL_MODEL_BASE_URL="http://YOUR_SERVER_IP:8000/v1"
export LOCAL_TEXT_MODEL="Qwen/Qwen2.5-VL-32B-Instruct"
export LOCAL_AUDIO_MODEL="Qwen/Qwen2-Audio-7B-Instruct"
```

Run `streamlit run app.py` or `uvicorn api:app` as usual — the sidebar
will show "Model backend: your own server" and the Gemini API key field
disappears, since it's not needed anymore.

**Try the base (non-fine-tuned) model first.** Qwen2.5-VL is already a
strong general-purpose model — it may grade acceptably out of the box.
Fine-tuning (below) is for closing the remaining gap, not a prerequisite
to using your own model at all.

### Step 3 — Collect training data (already running automatically)

`training_logger.py` logs every graded submission to
`training_data/raw_log.jsonl` (plus a copy of the input file in
`training_data/inputs/`) whenever `COLLECT_TRAINING_DATA=true` (the
default). This happens regardless of which backend is active — you can
start collecting data today, even while still using Gemini, and use that
data to fine-tune your local model later.

**Raw data isn't safe to train on as-is** — it may contain the model's
own mistakes (which is exactly the "too lenient" problem you ran into
earlier). Review a sample and promote only verified-correct examples to
the gold dataset:

```python
from training_logger import promote_to_gold

# Approve as-is:
promote_to_gold("the-example-id-from-raw_log.jsonl")

# Or supply a teacher-corrected version of the JSON:
promote_to_gold("the-example-id", corrected_output={...corrected JSON...})
```

(A simple review UI — a Streamlit page listing raw examples with an
edit box and an "approve" button — is a natural next addition once
you're ready to start reviewing at scale; ask if you'd like it built.)

Aim for at least a few hundred **gold** examples per subject/skill
before fine-tuning — more (thousands) is better. Quality matters more
than volume: 500 carefully verified examples beat 5,000 unreviewed ones.

### Step 4 — Fine-tune with LoRA once you have gold data

With 256GB VRAM across A100/H100s, LoRA/QLoRA fine-tuning of Qwen2.5-VL
(32B or even 72B) is very feasible — full fine-tuning is unnecessary and
slower for this kind of task-adaptation. Two well-maintained tools:

- **[Axolotl](https://github.com/axolotl-ai-cloud/axolotl)** — YAML-config-driven, supports Qwen2-VL, good defaults.
- **[Unsloth](https://github.com/unslothai/unsloth)** — faster/lighter LoRA training, actively adds new Qwen support.

Rough shape of the workflow (exact commands depend on the tool version
at the time — check its current docs):

1. Convert `training_data/gold.jsonl` into the tool's expected
   conversation format (system prompt + user message with image/audio +
   assistant message = the gold JSON).
2. Write a LoRA config: rank 16-64, targeting attention + MLP projection
   layers, 1-3 epochs over your gold set (multimodal fine-tunes overfit
   fast with small datasets — watch a held-out validation split).
3. Train, then merge the LoRA adapter into the base weights (or serve
   the adapter directly — vLLM supports LoRA adapters at serving time
   via `--enable-lora`).
4. Point `LOCAL_TEXT_MODEL` / `LOCAL_AUDIO_MODEL` at the new
   checkpoint name and restart vLLM. Nothing else changes.

### Honest expectations

- Steps 1-2 (serving a base open model) can be working within a day or
  two on your hardware.
- Step 3 (collecting enough good gold data) is the real bottleneck —
  this typically takes weeks to months of real usage, not a one-time task.
- Step 4 (fine-tuning) itself is fast once you have data (hours, given
  your GPUs) — most of the effort is in steps 2-3, not the training run.
- A fine-tuned 32B open model can get very close to Gemini's grading
  quality for well-represented subjects/skills with enough gold data,
  but matching Gemini's raw general reasoning across every possible
  edge case is unlikely — the honest goal is "good enough and fully
  independent," not "identical to Gemini."
