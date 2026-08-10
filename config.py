"""
config.py
Central configuration for the Homework Checker AI app.
All values can be overridden via environment variables so the app
works the same way locally, in Docker, or on a hosting platform.
"""

import os
from pathlib import Path

# If a .env file exists (see .env.example), load it into the environment
# automatically -- this is what makes `cp .env.example .env` + editing it
# actually work, on Windows/macOS/Linux alike, without needing to run
# export/set/$env: commands by hand every session. Real environment
# variables (e.g. set by your OS, shell profile, or process manager)
# still take precedence and are never overwritten by .env.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed -- fall back to real env vars only

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
REPORTS_DIR = BASE_DIR / "reports"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

for _dir in (UPLOAD_DIR, REPORTS_DIR, TEMPLATES_DIR, STATIC_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Gemini API
# ---------------------------------------------------------------------------
# Never hard-code a real key here. Set it as an environment variable:
#   export GEMINI_API_KEY="your-key-here"
# or type it into the sidebar text box when running the Streamlit app.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Available models the teacher can pick from in the UI.
# NOTE: Google retires/renames Gemini model IDs periodically. If you get a
# "404 ... is no longer available to new users" error, check the current
# list at https://ai.google.dev/gemini-api/docs/models and update below.
AVAILABLE_MODELS = [
    "gemini-3.5-flash",        # stable, best price-performance default
    "gemini-3.1-flash-lite",   # stable, fastest/cheapest option
    "gemini-3.1-pro-preview",  # preview, strongest reasoning for hard math
]
DEFAULT_MODEL = "gemini-3.5-flash"

# Generation settings
GENERATION_CONFIG = {
    "temperature": 0.1,       # low temperature -> consistent, strict grading
    "top_p": 0.9,
    "max_output_tokens": 8192,
    "response_mime_type": "application/json",
}

# ---------------------------------------------------------------------------
# Model backend switch: "gemini" (cloud API) or "local" (your own server)
# ---------------------------------------------------------------------------
# This is the ONE setting that determines whether homework is graded via
# Gemini's API or your own self-hosted model. Nothing else in the app
# (app.py, api.py, report_generator.py) needs to change when you switch.
#   export MODEL_BACKEND="local"
MODEL_BACKEND = os.environ.get("MODEL_BACKEND", "gemini")  # "gemini" | "local"

# Even when MODEL_BACKEND=local, Speaking (audio) grading keeps using
# Gemini by default, since mature self-hostable audio-understanding
# models are still hard to get running well on consumer hardware (e.g. a
# MacBook). Set to "false" once you have a real local audio model deployed
# and want Speaking to use it too.
#   export FORCE_GEMINI_FOR_SPEAKING="false"
FORCE_GEMINI_FOR_SPEAKING = os.environ.get("FORCE_GEMINI_FOR_SPEAKING", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Async job queue (Redis + RQ) -- for POST /jobs in api.py
# ---------------------------------------------------------------------------
# When many students submit at once (e.g. via a CRM), grading them
# synchronously inside the HTTP request risks slow responses, CRM
# timeouts, and Gemini rate-limit errors under load. POST /jobs instead
# enqueues the work and returns a job_id immediately; one or more
# background workers (run with `rq worker homework_grading`) process the
# queue, and the caller polls GET /jobs/{job_id} for the result.
#   export REDIS_URL="redis://localhost:6379/0"
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
JOB_QUEUE_NAME = os.environ.get("JOB_QUEUE_NAME", "homework_grading")
JOB_TIMEOUT_SECONDS = int(os.environ.get("JOB_TIMEOUT_SECONDS", "300"))  # max time per job
JOB_RESULT_TTL_SECONDS = int(os.environ.get("JOB_RESULT_TTL_SECONDS", str(60 * 60 * 24)))  # how long results stay fetchable

# ---------------------------------------------------------------------------
# Local model server (Phase 2: your own GPU server, e.g. vLLM)
# ---------------------------------------------------------------------------
# vLLM (and most self-hosted inference servers) expose an OpenAI-compatible
# REST API, so we talk to it the same way we'd talk to OpenAI's API --
# just pointed at your own server's address instead.
#   export LOCAL_MODEL_BASE_URL="http://YOUR_SERVER_IP:8000/v1"
LOCAL_MODEL_BASE_URL = os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:8000/v1")
# The API "key" your local server expects, if any. vLLM ignores this by
# default (any non-empty string works) unless you've configured it with
# --api-key yourself.
LOCAL_MODEL_API_KEY = os.environ.get("LOCAL_MODEL_API_KEY", "not-needed")

# Model names as registered on your local server. These are placeholders --
# set them to whatever you actually deploy (base model at first, later your
# fine-tuned checkpoint's name/path).
LOCAL_TEXT_MODEL = os.environ.get("LOCAL_TEXT_MODEL", "Qwen2.5-VL-32B-Instruct")
LOCAL_AUDIO_MODEL = os.environ.get("LOCAL_AUDIO_MODEL", "Qwen2-Audio-7B-Instruct")

# ---------------------------------------------------------------------------
# Training-data collection (for future fine-tuning)
# ---------------------------------------------------------------------------
# When enabled, every graded submission (input file + prompt + model
# output) is logged to TRAINING_DATA_DIR so you can build a fine-tuning
# dataset over time, with an optional teacher-correction step to turn raw
# model output into "gold" (verified-correct) training examples.
#   export COLLECT_TRAINING_DATA="false"   # to disable
COLLECT_TRAINING_DATA = os.environ.get("COLLECT_TRAINING_DATA", "true").lower() == "true"
TRAINING_DATA_DIR = BASE_DIR / "training_data"
TRAINING_INPUTS_DIR = TRAINING_DATA_DIR / "inputs"
TRAINING_RAW_LOG = TRAINING_DATA_DIR / "raw_log.jsonl"
TRAINING_GOLD_LOG = TRAINING_DATA_DIR / "gold.jsonl"

for _dir in (TRAINING_DATA_DIR, TRAINING_INPUTS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Upload constraints
# ---------------------------------------------------------------------------
# Audio extensions are needed for "Speaking" language-skill assessments,
# where the student uploads a recording of themselves talking.
ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx",
    ".mp3", ".wav", ".m4a", ".ogg",
}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg"}
MAX_FILE_SIZE_MB = 25

# ---------------------------------------------------------------------------
# REST API (api.py / FastAPI)
# ---------------------------------------------------------------------------
# If set, callers of the REST API must send this value in an
# "X-Internal-Api-Key" header. This protects your Gemini quota/billing
# from being hit by anonymous internet traffic once you deploy api.py
# somewhere public. Leave unset during local development if you don't
# need this yet.
#   export INTERNAL_API_KEY="some-long-random-string"
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")

# ---------------------------------------------------------------------------
# Grade scale (kept here so report_generator + app agree on labels/colors)
# ---------------------------------------------------------------------------
GRADE_SCALE = [
    (90, 100, "Excellent", "#16a34a"),
    (80, 89, "Very Good", "#22c55e"),
    (70, 79, "Good", "#84cc16"),
    (60, 69, "Satisfactory", "#eab308"),
    (50, 59, "Needs Improvement", "#f97316"),
    (0, 49, "Significant Improvement Needed", "#ef4444"),
]


def grade_for_score(score: float):
    for low, high, label, color in GRADE_SCALE:
        if low <= score <= high:
            return label, color
    return "Unknown", "#6b7280"
