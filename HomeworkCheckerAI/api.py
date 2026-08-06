"""
api.py
REST API for Homework Checker AI, built with FastAPI.

This runs ALONGSIDE the Streamlit app (app.py) -- it does not replace it.
Use this when another website, mobile app, or backend service needs to
submit a homework file (or a Speaking audio recording) programmatically
and get back the same JSON grading result the Streamlit UI shows, plus
endpoints to turn that result into HTML/PDF/Excel reports.

Run with:
    uvicorn api:app --reload --port 8000

Interactive API docs (Swagger UI) are then available at:
    http://localhost:8000/docs

Example (curl):
    curl -X POST http://localhost:8000/analyze \\
      -F "file=@homework.pdf" \\
      -F "subject_key=math" \\
      -F "api_key=YOUR_GEMINI_API_KEY"

Example (language skill, Speaking):
    curl -X POST http://localhost:8000/analyze \\
      -F "file=@recording.mp3" \\
      -F "language_key=english" \\
      -F "skill_key=speaking" \\
      -F "api_key=YOUR_GEMINI_API_KEY"
"""

from typing import Optional

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from config import DEFAULT_MODEL, INTERNAL_API_KEY, MODEL_BACKEND, COLLECT_TRAINING_DATA
from homework_checker import get_checker, HomeworkCheckerError
from prompts import (
    DEFAULT_LANGUAGE_KEY,
    DEFAULT_SKILL_KEY,
    DEFAULT_SUBJECT_KEY,
    LANGUAGES,
    SKILL_TEMPLATES,
    SUBJECTS,
)
from report_generator import (
    generate_excel_gradebook,
    generate_html_report,
    generate_pdf_report,
)
from utils import (
    FileTooLargeError,
    UnsupportedFileError,
    save_uploaded_file,
    validate_upload,
)
import training_logger
import jobs

app = FastAPI(
    title="Homework Checker AI API",
    description=(
        "Upload a student's homework (PDF, image, Word doc, or an audio "
        "recording for Speaking tasks) and receive AI-graded, "
        "question-by-question feedback as JSON, plus optional HTML/PDF/"
        "Excel reports generated from that result."
    ),
    version="1.0.0",
)

# Tighten allow_origins to your actual frontend domain(s) before deploying
# this publicly -- "*" is convenient for local development/testing only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_internal_key(x_internal_api_key: Optional[str]) -> None:
    """
    If INTERNAL_API_KEY is configured (see config.py), every request must
    include a matching X-Internal-Api-Key header. This is what stops
    random internet traffic from burning through your Gemini quota/billing
    once this API is deployed somewhere reachable. If INTERNAL_API_KEY is
    left empty, the API stays open (fine for local-only development).
    """
    if INTERNAL_API_KEY and x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid X-Internal-Api-Key header.",
        )


def _strip_student_name(payload: dict) -> dict:
    """/analyze responses embed student_name alongside the grading JSON;
    strip it back out before feeding the dict to the report generators,
    which expect only the grading schema."""
    return {k: v for k, v in payload.items() if k != "student_name"}


# ---------------------------------------------------------------------------
# Discovery endpoints -- so a calling app can build its own subject/
# language/skill picker without hard-coding the list on its own side.
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/backend")
def backend_info():
    """Reports which model backend this server is currently configured to use."""
    stats = training_logger.count_examples()
    return {
        "model_backend": MODEL_BACKEND,
        "training_data_collection_enabled": COLLECT_TRAINING_DATA,
        "training_examples_logged": stats,
    }


@app.get("/subjects")
def list_subjects():
    """Standard subjects usable as `subject_key` in POST /analyze."""
    return {key: profile.label for key, profile in SUBJECTS.items()}


@app.get("/languages")
def list_languages():
    """Languages usable as `language_key` in POST /analyze."""
    return LANGUAGES


@app.get("/skills")
def list_skills():
    """Language skills usable as `skill_key` in POST /analyze."""
    return {key: tmpl["label"] for key, tmpl in SKILL_TEMPLATES.items()}


# ---------------------------------------------------------------------------
# Core grading endpoint
# ---------------------------------------------------------------------------
@app.post("/analyze")
def analyze_homework(
    file: UploadFile = File(..., description="Homework file: PDF, PNG/JPG/WEBP, DOCX, or audio (mp3/wav/m4a/ogg) for Speaking."),
    student_name: str = Form("Student"),
    model_name: Optional[str] = Form(None, description="Model name override. Defaults to config.DEFAULT_MODEL (Gemini) or the configured local model, depending on MODEL_BACKEND."),
    subject_key: str = Form(DEFAULT_SUBJECT_KEY, description="Standard subject key, e.g. 'math', 'physics', 'general'. Ignored if language_key+skill_key are set."),
    custom_subject_name: str = Form("", description="Required only when subject_key='general'."),
    language_key: str = Form("", description="e.g. 'english', 'russian', 'turkish'. Set together with skill_key to grade a language skill instead of a standard subject."),
    skill_key: str = Form("", description="e.g. 'writing', 'reading', 'listening', 'speaking'."),
    extra_instructions: str = Form("", description="Optional extra context, e.g. 'CEFR level: B1' or 'Be strict about rigor.'"),
    api_key: Optional[str] = Form(None, description="Gemini API key. Ignored entirely when MODEL_BACKEND=local. Optional if GEMINI_API_KEY is set as a server env var."),
    x_internal_api_key: Optional[str] = Header(None),
):
    """
    Grades one homework submission and returns the same JSON schema the
    Streamlit app displays: overall_score, grade, questions[], summary.
    Backend (Gemini vs your own server) is controlled by MODEL_BACKEND.

    This call is SYNCHRONOUS -- it blocks until grading finishes (often
    10-30+ seconds). Fine for testing or low-volume use. If many
    submissions can arrive at once (e.g. from a CRM), use POST /jobs
    instead: it returns instantly and you poll GET /jobs/{job_id} for
    the result, so nothing times out and nothing blocks under load.
    """
    _check_internal_key(x_internal_api_key)

    file_bytes = file.file.read()
    try:
        validate_upload(file.filename, len(file_bytes))
    except (UnsupportedFileError, FileTooLargeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if subject_key == "general" and not language_key and not custom_subject_name.strip():
        raise HTTPException(
            status_code=400,
            detail="custom_subject_name is required when subject_key='general'.",
        )

    saved_path = save_uploaded_file(file_bytes, file.filename)

    try:
        checker = get_checker(
            api_key=api_key or None,
            model_name=model_name,
            subject_key=subject_key,
            custom_subject_name=custom_subject_name,
            language_key=language_key,
            skill_key=skill_key,
        )
        result = checker.analyze(saved_path, extra_instructions=extra_instructions)

        if COLLECT_TRAINING_DATA:
            training_logger.log_raw_example(
                input_file_path=saved_path,
                system_prompt=getattr(checker, "system_prompt", ""),
                user_prompt=getattr(checker, "last_user_prompt", ""),
                model_output=result,
                subject_key=subject_key,
                custom_subject_name=custom_subject_name,
                language_key=language_key,
                skill_key=skill_key,
                model_name=getattr(checker, "model_name", model_name or ""),
                backend=MODEL_BACKEND,
            )
    except HomeworkCheckerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    return {"student_name": student_name, **result}


# ---------------------------------------------------------------------------
# Async job queue -- recommended for CRM/production use. POST /jobs returns
# instantly (milliseconds); grading happens in a separate `rq worker`
# process. Poll GET /jobs/{job_id} until status is "finished" or "failed".
# Requires Redis running and at least one worker: see README.md.
# ---------------------------------------------------------------------------
@app.post("/jobs")
def submit_job(
    file: UploadFile = File(..., description="Homework file: PDF, PNG/JPG/WEBP, DOCX, or audio (mp3/wav/m4a/ogg) for Speaking."),
    student_name: str = Form("Student"),
    model_name: Optional[str] = Form(None),
    subject_key: str = Form(DEFAULT_SUBJECT_KEY),
    custom_subject_name: str = Form(""),
    language_key: str = Form(""),
    skill_key: str = Form(""),
    extra_instructions: str = Form(""),
    api_key: Optional[str] = Form(None),
    x_internal_api_key: Optional[str] = Header(None),
):
    """
    Enqueues one homework submission for grading and returns a job_id
    immediately -- it does NOT wait for grading to finish. Poll
    GET /jobs/{job_id} to retrieve the result once status is "finished".
    """
    _check_internal_key(x_internal_api_key)

    file_bytes = file.file.read()
    try:
        validate_upload(file.filename, len(file_bytes))
    except (UnsupportedFileError, FileTooLargeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if subject_key == "general" and not language_key and not custom_subject_name.strip():
        raise HTTPException(
            status_code=400,
            detail="custom_subject_name is required when subject_key='general'.",
        )

    saved_path = save_uploaded_file(file_bytes, file.filename)

    try:
        job_id = jobs.enqueue_grading_job(
            saved_path_str=str(saved_path),
            student_name=student_name,
            api_key=api_key or "",
            model_name=model_name or "",
            subject_key=subject_key,
            custom_subject_name=custom_subject_name,
            language_key=language_key,
            skill_key=skill_key,
            extra_instructions=extra_instructions,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not reach the job queue (is Redis running?): {exc}",
        )

    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def check_job(job_id: str, x_internal_api_key: Optional[str] = Header(None)):
    """
    Returns the current status of a job submitted via POST /jobs:
    "queued" | "started" | "finished" | "failed" | "not_found".
    When "finished", the response includes "result" (the grading JSON,
    same shape as /analyze). When "failed", it includes a short "error".
    """
    _check_internal_key(x_internal_api_key)
    return jobs.get_job_status(job_id)


# ---------------------------------------------------------------------------
# Report generation endpoints -- feed back a previous /analyze response
# (as-is, including "student_name") to get a downloadable file.
# ---------------------------------------------------------------------------
@app.post("/reports/html")
def report_html(
    payload: dict = Body(..., description="The full JSON body returned by POST /analyze."),
    x_internal_api_key: Optional[str] = Header(None),
):
    _check_internal_key(x_internal_api_key)
    student_name = payload.get("student_name", "Student")
    result = _strip_student_name(payload)
    path = generate_html_report(result, student_name=student_name)
    return FileResponse(path, filename=path.name, media_type="text/html")


@app.post("/reports/pdf")
def report_pdf(
    payload: dict = Body(..., description="The full JSON body returned by POST /analyze."),
    x_internal_api_key: Optional[str] = Header(None),
):
    _check_internal_key(x_internal_api_key)
    student_name = payload.get("student_name", "Student")
    result = _strip_student_name(payload)
    path = generate_pdf_report(result, student_name=student_name)
    return FileResponse(path, filename=path.name, media_type="application/pdf")


@app.post("/reports/gradebook")
def report_gradebook(
    records: list = Body(..., description="A list of /analyze response bodies (each including student_name) to combine into one gradebook."),
    x_internal_api_key: Optional[str] = Header(None),
):
    _check_internal_key(x_internal_api_key)
    if not records:
        raise HTTPException(status_code=400, detail="records must contain at least one item.")
    tuples = [(r.get("student_name", "Student"), _strip_student_name(r)) for r in records]
    path = generate_excel_gradebook(tuples)
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
