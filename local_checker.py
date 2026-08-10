"""
local_checker.py
Grading backend for a self-hosted, open-weight model -- Phase 2 of the
project: full independence from any cloud AI provider.

This talks to YOUR OWN inference server via its OpenAI-compatible REST
API. That covers vLLM, TGI, SGLang, Ollama, or anything else speaking
that protocol -- just point config.LOCAL_MODEL_BASE_URL at it. Nothing
else in the app (app.py, api.py, report_generator.py) needs to change;
homework_checker.get_checker() picks this class automatically when
MODEL_BACKEND=local.

Important difference from the Gemini backend: open vision-language
models (e.g. Qwen2.5-VL) take IMAGES as input, not raw PDF bytes like
Gemini can. PDFs are converted locally to page images (via PyMuPDF)
before being sent, so this client still accepts the exact same file
types the rest of the app does -- the conversion is invisible to callers.
"""

import base64
import time
from pathlib import Path
from typing import Optional

from config import (
    GENERATION_CONFIG,
    LOCAL_AUDIO_MODEL,
    LOCAL_MODEL_API_KEY,
    LOCAL_MODEL_BASE_URL,
    LOCAL_TEXT_MODEL,
)
from prompts import DEFAULT_SUBJECT_KEY, build_system_prompt, build_user_prompt
from utils import (
    InvalidModelResponseError,
    extract_text_from_docx,
    guess_mime_type,
    parse_and_validate_json,
)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg"}
PDF_PAGE_DPI = 150
MAX_PDF_PAGES = 12  # safety cap so a huge PDF can't blow past the model's context window


class LocalCheckerError(Exception):
    pass


def _pdf_to_image_data_urls(file_path: Path) -> list:
    """
    Renders each page of a PDF to a PNG and returns base64 data URIs.
    Open vision-language models take images, not raw PDF bytes -- this
    bridges that gap transparently.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise LocalCheckerError(
            "PyMuPDF is required to send PDFs to a local vision model. "
            "Install it with: pip install pymupdf"
        ) from exc

    doc = fitz.open(str(file_path))
    try:
        zoom = PDF_PAGE_DPI / 72
        matrix = fitz.Matrix(zoom, zoom)
        data_urls = []
        for i, page in enumerate(doc):
            if i >= MAX_PDF_PAGES:
                break
            pix = page.get_pixmap(matrix=matrix)
            b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
            data_urls.append(f"data:image/png;base64,{b64}")
        return data_urls
    finally:
        doc.close()


def _image_to_data_url(file_path: Path) -> str:
    mime_type = guess_mime_type(file_path) or "image/png"
    b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


class LocalModelChecker:
    """
    Mirrors HomeworkChecker's public interface exactly (same constructor
    args, same .analyze() signature/return shape) so homework_checker.
    get_checker() can hand back either class interchangeably.

    Usage:
        checker = LocalModelChecker(subject_key="math")
        result = checker.analyze(Path("uploads/hw1.pdf"))
    """

    def __init__(
        self,
        api_key: Optional[str] = None,  # unused; kept only for interface parity with HomeworkChecker
        model_name: str = "",
        subject_key: str = DEFAULT_SUBJECT_KEY,
        custom_subject_name: str = "",
        language_key: str = "",
        skill_key: str = "",
    ):
        self.subject_key = subject_key
        self.custom_subject_name = custom_subject_name
        self.language_key = language_key
        self.skill_key = skill_key
        self.is_speaking = skill_key == "speaking"

        # Speaking needs an audio-capable model; everything else needs the
        # vision-language model. Explicit model_name always wins.
        self.model_name = model_name or (LOCAL_AUDIO_MODEL if self.is_speaking else LOCAL_TEXT_MODEL)

        # Imported lazily so the rest of the app can run without the
        # `openai` package installed if you're only using the Gemini backend.
        from openai import OpenAI

        self.client = OpenAI(base_url=LOCAL_MODEL_BASE_URL, api_key=LOCAL_MODEL_API_KEY)
        self.system_prompt = build_system_prompt(
            subject_key=subject_key,
            custom_subject_name=custom_subject_name,
            language_key=language_key,
            skill_key=skill_key,
        )

    def _build_user_content(self, file_path: Path, extra_instructions: str) -> list:
        """Builds an OpenAI-style multimodal `content` list for the chat message."""
        ext = file_path.suffix.lower()
        text = build_user_prompt(extra_instructions)
        self.last_user_prompt = text
        content = []

        if ext == ".docx":
            text += (
                "\n\nThe homework was submitted as a Word document. Here is "
                "the extracted text:\n\n" + extract_text_from_docx(file_path)
            )
            content.append({"type": "text", "text": text})
        elif ext in AUDIO_EXTENSIONS:
            content.append({"type": "text", "text": text})
            audio_format = ext.lstrip(".")
            b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
            # OpenAI-style audio content part. Whether your server actually
            # supports this depends on the served model (e.g. Qwen2-Audio)
            # and your inference server's version -- check its docs if this
            # errors out.
            content.append({
                "type": "input_audio",
                "input_audio": {"data": b64, "format": audio_format},
            })
        elif ext == ".pdf":
            content.append({"type": "text", "text": text})
            for data_url in _pdf_to_image_data_urls(file_path):
                content.append({"type": "image_url", "image_url": {"url": data_url}})
        else:
            content.append({"type": "text", "text": text})
            content.append({"type": "image_url", "image_url": {"url": _image_to_data_url(file_path)}})

        return content

    def analyze(
        self,
        file_path: Path,
        extra_instructions: str = "",
        max_retries: int = 2,
    ) -> dict:
        file_path = Path(file_path)
        try:
            user_content = self._build_user_content(file_path, extra_instructions)
        except LocalCheckerError:
            raise
        except Exception as exc:
            raise LocalCheckerError(f"Failed to prepare '{file_path.name}' for the local model: {exc}") from exc

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]

        last_error = None
        raw_text = ""
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=GENERATION_CONFIG.get("temperature", 0.1),
                    top_p=GENERATION_CONFIG.get("top_p", 0.9),
                    max_tokens=GENERATION_CONFIG.get("max_output_tokens", 8192),
                    response_format={"type": "json_object"},
                )
                raw_text = response.choices[0].message.content
                return parse_and_validate_json(raw_text)
            except InvalidModelResponseError as exc:
                last_error = exc
                messages.append({"role": "assistant", "content": raw_text})
                messages.append({
                    "role": "user",
                    "content": "Your previous response was not valid JSON matching the "
                               "required schema. Return ONLY the corrected JSON object, nothing else.",
                })
                time.sleep(1)
            except Exception as exc:  # connection errors, server errors, etc.
                last_error = exc
                time.sleep(1.5 * (attempt + 1))

        raise LocalCheckerError(
            f"Failed to get a valid analysis from the local model after "
            f"{max_retries + 1} attempts: {last_error}"
        )

    def analyze_batch(self, file_paths, extra_instructions: str = ""):
        results = []
        for path in file_paths:
            try:
                result = self.analyze(path, extra_instructions=extra_instructions)
                results.append((path, result, None))
            except Exception as exc:
                results.append((path, None, str(exc)))
        return results
