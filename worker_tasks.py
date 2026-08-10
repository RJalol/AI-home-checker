"""
worker_tasks.py
The actual grading work, executed by RQ worker processes -- NOT by the
API process itself. This is what lets api.py's POST /jobs return
instantly: it just hands this function's name + arguments to Redis, and
a separate `rq worker` process (started with `rq worker homework_grading`)
picks it up and runs it whenever it's free.

This must be a plain, importable module-level function (not a closure or
a method) because RQ pickles a reference to it (module path + function
name) to store in Redis -- the worker process re-imports it to run the job.
"""

from pathlib import Path

from config import COLLECT_TRAINING_DATA, MODEL_BACKEND
from homework_checker import get_checker
import training_logger


def grade_homework_task(
    saved_path_str: str,
    student_name: str = "Student",
    api_key: str = "",
    model_name: str = "",
    subject_key: str = "math",
    custom_subject_name: str = "",
    language_key: str = "",
    skill_key: str = "",
    extra_instructions: str = "",
) -> dict:
    """
    Grades one homework submission. Runs inside a worker process, so any
    exception raised here is captured by RQ as the job's failure info
    (visible via GET /jobs/{id}) rather than crashing the API.

    Returns the same JSON schema POST /analyze always returned -- RQ
    stores this dict as the job's `.result`.
    """
    saved_path = Path(saved_path_str)

    checker = get_checker(
        api_key=api_key or None,
        model_name=model_name or None,
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

    return {"student_name": student_name, **result}
