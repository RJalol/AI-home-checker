"""
homework_checker.py
The core backend: takes an uploaded homework file, sends it (plus the
grading system prompt) to Gemini, and returns validated, structured JSON.

PDFs and images are sent directly to Gemini as multimodal input, since
the model can read handwriting and typeset math from the page itself.
DOCX files are text-extracted first (Gemini has no native docx reader).
"""

import time
from pathlib import Path
from typing import Optional

from config import (
    GEMINI_API_KEY,
    DEFAULT_MODEL,
    GENERATION_CONFIG,
)
from prompts import build_system_prompt, build_user_prompt, DEFAULT_SUBJECT_KEY
from utils import (
    extract_text_from_docx,
    parse_and_validate_json,
    guess_mime_type,
    InvalidModelResponseError,
)

# Gemini accepts small files as "inline data" (raw bytes in the request
# body) or as uploads via the resumable File API. Inline is simpler and
# avoids a known File API code path (genai.upload_file) that, on some
# accounts/SDK versions, incorrectly routes through a Google API
# "discovery" endpoint and fails with a misleading "API key not valid"
# error even though the key is valid. We use inline data for anything
# reasonably small (homework pages, short audio recordings) and only
# fall back to the File API for larger files, per Google's own guidance
# (inline is recommended for requests under ~20MB total).
INLINE_SIZE_LIMIT_BYTES = 15 * 1024 * 1024  # 15 MB


class HomeworkCheckerError(Exception):
    pass


class HomeworkChecker:
    """
    Thin wrapper around the Gemini API dedicated to homework grading.

    Usage:
        # Standard subject:
        checker = HomeworkChecker(api_key="...", model_name="gemini-3.5-flash",
                                   subject_key="physics")
        # Language skill:
        checker = HomeworkChecker(api_key="...", model_name="gemini-3.5-flash",
                                   language_key="english", skill_key="speaking")
        result = checker.analyze(Path("uploads/hw1.pdf"))
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = DEFAULT_MODEL,
        subject_key: str = DEFAULT_SUBJECT_KEY,
        custom_subject_name: str = "",
        language_key: str = "",
        skill_key: str = "",
    ):
        self.api_key = api_key or GEMINI_API_KEY
        if not self.api_key:
            raise HomeworkCheckerError(
                "No Gemini API key found. Set the GEMINI_API_KEY environment "
                "variable or enter a key in the sidebar."
            )
        self.model_name = model_name
        self.subject_key = subject_key
        self.custom_subject_name = custom_subject_name
        self.language_key = language_key
        self.skill_key = skill_key

        # Imported lazily so the rest of the app (and tests) can run
        # without the google-generativeai package installed.
        import google.generativeai as genai

        genai.configure(api_key=self.api_key)
        self._genai = genai
        self.system_prompt = build_system_prompt(
            subject_key=subject_key,
            custom_subject_name=custom_subject_name,
            language_key=language_key,
            skill_key=skill_key,
        )
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.system_prompt,
        )

    def analyze(
        self,
        file_path: Path,
        extra_instructions: str = "",
        max_retries: int = 2,
    ) -> dict:
        """
        Analyzes a single homework file and returns validated JSON (dict).
        Retries once or twice if the model returns malformed JSON, asking
        it to correct itself.
        """
        file_path = Path(file_path)
        ext = file_path.suffix.lower()
        user_prompt = build_user_prompt(extra_instructions)
        self.last_user_prompt = user_prompt

        if ext == ".docx":
            content_parts = [
                user_prompt,
                "The homework was submitted as a Word document. "
                "Here is the extracted text:\n\n" + extract_text_from_docx(file_path),
            ]
        else:
            mime_type = guess_mime_type(file_path)
            file_size = file_path.stat().st_size
            try:
                if file_size <= INLINE_SIZE_LIMIT_BYTES:
                    # Inline data: just the raw bytes + mime type, sent
                    # directly in the generate_content request. No
                    # separate upload step, no File API involved.
                    file_bytes = file_path.read_bytes()
                    content_parts = [
                        user_prompt,
                        {"mime_type": mime_type, "data": file_bytes},
                    ]
                else:
                    uploaded = self._genai.upload_file(
                        path=str(file_path),
                        mime_type=mime_type,
                    )
                    content_parts = [user_prompt, uploaded]
            except Exception as exc:
                # Upload/read failures (bad mime type, unsupported format,
                # size limits, transient network issues) can surface as
                # confusing low-level SDK/HTTP errors. Wrap them clearly
                # so it's obvious this happened while preparing the file,
                # not while calling generate_content / grading.
                raise HomeworkCheckerError(
                    f"Failed to prepare '{file_path.name}' (detected mime type: "
                    f"{mime_type}) for Gemini: {exc}"
                ) from exc

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                response = self.model.generate_content(
                    content_parts,
                    generation_config=GENERATION_CONFIG,
                )
                return parse_and_validate_json(response.text)
            except InvalidModelResponseError as exc:
                last_error = exc
                # Ask the model to fix its own output on retry.
                content_parts = content_parts + [
                    "Your previous response was not valid JSON matching the "
                    "required schema. Return ONLY the corrected JSON object, "
                    "nothing else."
                ]
                time.sleep(1)
            except Exception as exc:  # network / API errors
                last_error = exc
                time.sleep(1.5 * (attempt + 1))

        raise HomeworkCheckerError(
            f"Failed to get a valid analysis after {max_retries + 1} attempts: {last_error}"
        )

    def analyze_batch(self, file_paths, extra_instructions: str = ""):
        """Analyzes multiple files, returning a list of (path, result_or_error)."""
        results = []
        for path in file_paths:
            try:
                result = self.analyze(path, extra_instructions=extra_instructions)
                results.append((path, result, None))
            except Exception as exc:
                results.append((path, None, str(exc)))
        return results


def get_checker(
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    subject_key: str = DEFAULT_SUBJECT_KEY,
    custom_subject_name: str = "",
    language_key: str = "",
    skill_key: str = "",
    backend: Optional[str] = None,
):
    """
    Factory that returns either the Gemini-backed HomeworkChecker or the
    self-hosted LocalModelChecker, depending on config.MODEL_BACKEND (or
    the explicit `backend` argument, which overrides it for one call).

    Both classes expose the identical .analyze(file_path, extra_instructions)
    interface and return the same JSON schema, so callers (app.py, api.py)
    never need to know or care which one they got back. This is the ONE
    place in the codebase where the Gemini-vs-local decision is made.

    Special case: if config.FORCE_GEMINI_FOR_SPEAKING is true (the default)
    and this call is for skill_key="speaking", Gemini is used even when
    MODEL_BACKEND=local. Mature, easy-to-self-host audio-understanding
    models are still hard to come by (especially on consumer hardware like
    a MacBook), so this keeps Speaking working well while you experiment
    with a local model for everything else. Set
    FORCE_GEMINI_FOR_SPEAKING=false once you have a real local audio model
    running and want to test it.
    """
    from config import DEFAULT_MODEL as _DEFAULT_GEMINI_MODEL
    from config import FORCE_GEMINI_FOR_SPEAKING, MODEL_BACKEND

    chosen_backend = (backend or MODEL_BACKEND or "gemini").lower()

    if skill_key == "speaking" and chosen_backend == "local" and FORCE_GEMINI_FOR_SPEAKING:
        chosen_backend = "gemini"

    if chosen_backend == "local":
        from local_checker import LocalModelChecker

        return LocalModelChecker(
            api_key=api_key,
            model_name=model_name or "",
            subject_key=subject_key,
            custom_subject_name=custom_subject_name,
            language_key=language_key,
            skill_key=skill_key,
        )

    return HomeworkChecker(
        api_key=api_key,
        model_name=model_name or _DEFAULT_GEMINI_MODEL,
        subject_key=subject_key,
        custom_subject_name=custom_subject_name,
        language_key=language_key,
        skill_key=skill_key,
    )
