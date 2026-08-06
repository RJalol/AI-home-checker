"""
app.py
Streamlit frontend for Homework Checker AI.

Run with:
    streamlit run app.py

Flow:
  Upload Homework -> Python Backend (homework_checker.py) -> Gemini API
  -> JSON Response -> Generate HTML / PDF / Excel reports -> Teacher Dashboard
"""

import streamlit as st
from pathlib import Path

from config import AVAILABLE_MODELS, DEFAULT_MODEL, MODEL_BACKEND, COLLECT_TRAINING_DATA, grade_for_score
from homework_checker import get_checker, HomeworkCheckerError
from prompts import SUBJECTS, DEFAULT_SUBJECT_KEY, LANGUAGES, DEFAULT_LANGUAGE_KEY, SKILL_TEMPLATES, DEFAULT_SKILL_KEY
from report_generator import (
    generate_html_report,
    generate_pdf_report,
    generate_excel_gradebook,
)
from utils import (
    validate_upload,
    save_uploaded_file,
    UnsupportedFileError,
    FileTooLargeError,
)
import training_logger

SUBJECT_KEYS = list(SUBJECTS.keys())
SUBJECT_LABELS = {key: SUBJECTS[key].label for key in SUBJECT_KEYS}
LANGUAGE_KEYS = list(LANGUAGES.keys())
SKILL_KEYS = list(SKILL_TEMPLATES.keys())
SKILL_LABELS = {key: SKILL_TEMPLATES[key]["label"] for key in SKILL_KEYS}

st.set_page_config(
    page_title="Homework Checker AI",
    page_icon="🧮",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state: keeps a running "gradebook" of everything graded this
# session so the teacher can export one combined Excel file at the end.
# ---------------------------------------------------------------------------
if "records" not in st.session_state:
    st.session_state.records = []  # list of (student_name, result_dict)
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "last_student" not in st.session_state:
    st.session_state.last_student = None


# ---------------------------------------------------------------------------
# Sidebar: API key + model + teacher context
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Settings")

    if MODEL_BACKEND == "local":
        st.info("🖥️ Model backend: **your own server** (MODEL_BACKEND=local)")
        api_key = None
        model_name = None
    else:
        st.caption("Model backend: **Gemini API** (set `MODEL_BACKEND=local` to use your own server instead)")
        api_key = st.text_input(
            "Gemini API Key",
            type="password",
            help="Or set the GEMINI_API_KEY environment variable instead.",
        )
        model_name = st.selectbox("Model", AVAILABLE_MODELS, index=AVAILABLE_MODELS.index(DEFAULT_MODEL))

    assessment_type = st.radio(
        "Assessment type",
        ["Standard subject", "Language skill"],
        horizontal=True,
    )

    subject_key = DEFAULT_SUBJECT_KEY
    custom_subject_name = ""
    language_key = ""
    skill_key = ""

    if assessment_type == "Standard subject":
        subject_key = st.selectbox(
            "Subject",
            SUBJECT_KEYS,
            index=SUBJECT_KEYS.index(DEFAULT_SUBJECT_KEY),
            format_func=lambda k: SUBJECT_LABELS[k],
            help="Grading criteria and error categories adapt to the subject you pick.",
        )
        if subject_key == "general":
            custom_subject_name = st.text_input(
                "Type the subject name",
                placeholder="e.g. Music Theory, Economics, Philosophy...",
            )
    else:
        col_lang, col_skill = st.columns(2)
        with col_lang:
            language_key = st.selectbox(
                "Language",
                LANGUAGE_KEYS,
                index=LANGUAGE_KEYS.index(DEFAULT_LANGUAGE_KEY),
                format_func=lambda k: LANGUAGES[k],
            )
        with col_skill:
            skill_key = st.selectbox(
                "Skill",
                SKILL_KEYS,
                index=SKILL_KEYS.index(DEFAULT_SKILL_KEY),
                format_func=lambda k: SKILL_LABELS[k],
            )
        if skill_key == "speaking":
            st.caption("🎙️ Upload an audio recording (.mp3, .wav, .m4a, .ogg) of the student speaking.")

    extra_instructions = st.text_area(
        "Extra grading context (optional)",
        placeholder="e.g. This is a Calculus II midterm. Be strict about rigor. / CEFR level: B1.",
    )

    if COLLECT_TRAINING_DATA:
        st.divider()
        st.subheader("📦 Training data (Phase 3 prep)")
        stats = training_logger.count_examples()
        st.caption(f"Raw examples logged: **{stats['raw']}** · Gold (reviewed) examples: **{stats['gold']}**")
        st.caption("These accumulate automatically for future fine-tuning. See README for how to review and promote them to 'gold'.")

    st.divider()
    st.subheader("📊 Session Gradebook")
    st.caption(f"{len(st.session_state.records)} submission(s) graded this session")
    if st.session_state.records:
        gb_path = generate_excel_gradebook(st.session_state.records)
        with open(gb_path, "rb") as f:
            st.download_button(
                "⬇️ Download Combined Gradebook (.xlsx)",
                data=f,
                file_name=gb_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        if st.button("Clear session gradebook", use_container_width=True):
            st.session_state.records = []
            st.rerun()


# ---------------------------------------------------------------------------
# Main: Upload + Analyze
# ---------------------------------------------------------------------------
st.title("🧮 Homework Checker AI")
if assessment_type == "Language skill":
    _subject_display = f"{LANGUAGES[language_key]} · {SKILL_LABELS[skill_key]}"
else:
    _subject_display = custom_subject_name.strip() if (subject_key == "general" and custom_subject_name.strip()) else SUBJECT_LABELS[subject_key]
st.caption(
    f"Subject: **{_subject_display}** · Upload a student's homework (PDF, image, Word "
    "doc, or audio recording) for automatic, question-by-question grading and feedback."
)

col_upload, col_name = st.columns([3, 1])
with col_upload:
    uploaded_file = st.file_uploader(
        "Upload homework",
        type=["pdf", "png", "jpg", "jpeg", "webp", "docx", "mp3", "wav", "m4a", "ogg"],
    )
with col_name:
    student_name = st.text_input("Student name", value="Student")

analyze_clicked = st.button("🔍 Analyze Homework", type="primary", disabled=uploaded_file is None)

if analyze_clicked and uploaded_file is not None:
    try:
        validate_upload(uploaded_file.name, uploaded_file.size)
    except (UnsupportedFileError, FileTooLargeError) as exc:
        st.error(str(exc))
        st.stop()

    if MODEL_BACKEND != "local" and not (api_key or "").strip():
        from config import GEMINI_API_KEY
        if not GEMINI_API_KEY:
            st.error("Please enter a Gemini API key in the sidebar (or set GEMINI_API_KEY).")
            st.stop()

    if assessment_type == "Standard subject" and subject_key == "general" and not custom_subject_name.strip():
        st.error("Please type the subject name in the sidebar (you selected 'General / Other').")
        st.stop()

    saved_path = save_uploaded_file(uploaded_file.getvalue(), uploaded_file.name)

    spinner_label = f"Analyzing {uploaded_file.name}" + (f" with {model_name}..." if model_name else " with your local model...")
    with st.spinner(spinner_label):
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
            st.error(f"Analysis failed: {exc}")
            st.stop()
        except Exception as exc:
            st.error(f"Unexpected error: {exc}")
            st.stop()

    st.session_state.last_result = result
    st.session_state.last_student = student_name
    st.session_state.records.append((student_name, result))
    st.success("Analysis complete!")


# ---------------------------------------------------------------------------
# Results display
# ---------------------------------------------------------------------------
result = st.session_state.last_result
student_name_display = st.session_state.last_student

if result:
    st.divider()
    overall_label, overall_color = grade_for_score(result["overall_score"])

    top1, top2, top3 = st.columns([1, 1, 2])
    top1.metric("Overall Score", f"{result['overall_score']}/100")
    top2.metric("Grade", result.get("grade", overall_label))
    top3.markdown(
        f"<div style='padding-top:10px'><span style='background:{overall_color};"
        f"color:white;padding:6px 14px;border-radius:8px;font-weight:600;'>"
        f"{result.get('grade', overall_label)}</span></div>",
        unsafe_allow_html=True,
    )

    st.subheader(f"Question-by-Question Feedback ({len(result['questions'])} detected)")
    for q in result["questions"]:
        label, color = grade_for_score(q["score"])
        with st.expander(
            f"Question {q['question_number']}  ·  {q['score']}/100  ·  {label}  ·  {q.get('difficulty', '')}"
        ):
            st.markdown(f"**Question:** {q['question']}")
            st.markdown(f"**Student answer:** {q['student_answer']}")
            st.markdown(f"**Expected solution:** {q['expected_solution']}")
            st.markdown("**Analysis:**")
            st.write(q["analysis"])

            if q.get("mistakes"):
                st.markdown("**Mistakes:**")
                for m in q["mistakes"]:
                    st.markdown(f"- {m}")

            if q.get("error_categories"):
                st.markdown("**Error categories:** " + ", ".join(
                    f"`{c}`" for c in q["error_categories"]
                ))

            st.markdown(f"**Correct answer:** {q['correct_answer']}")

            if q.get("suggestions"):
                st.markdown("**Suggestions:**")
                for s in q["suggestions"]:
                    st.markdown(f"- {s}")

    st.subheader("Final Summary")
    summary = result.get("summary", {})
    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**✅ Strengths**")
        for s in summary.get("strengths", []):
            st.markdown(f"- {s}")
        st.markdown("**📚 Topics to Review**")
        for t in summary.get("topics_to_review", []):
            st.markdown(f"- {t}")
    with s2:
        st.markdown("**⚠️ Weaknesses**")
        for w in summary.get("weaknesses", []):
            st.markdown(f"- {w}")
        st.markdown("**💡 Study Recommendations**")
        for r in summary.get("recommendations", []):
            st.markdown(f"- {r}")

    st.divider()
    st.subheader("📥 Export Report")
    e1, e2 = st.columns(2)

    html_path = generate_html_report(result, student_name=student_name_display)
    pdf_path = generate_pdf_report(result, student_name=student_name_display)

    with e1:
        with open(html_path, "rb") as f:
            st.download_button(
                "⬇️ Download HTML Report",
                data=f,
                file_name=html_path.name,
                mime="text/html",
                use_container_width=True,
            )
    with e2:
        with open(pdf_path, "rb") as f:
            st.download_button(
                "⬇️ Download PDF Report",
                data=f,
                file_name=pdf_path.name,
                mime="application/pdf",
                use_container_width=True,
            )
else:
    st.info("Upload a homework file and click **Analyze Homework** to get started.")
