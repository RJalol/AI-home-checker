"""
training_logger.py
Collects (input, model output) pairs from real grading sessions so you
can build a fine-tuning dataset over time -- this is what feeds Phase 3
(LoRA fine-tuning) later.

Two files are maintained under config.TRAINING_DATA_DIR:

  raw_log.jsonl   -- every graded submission, as-is (unverified). Cheap
                     to accumulate, but may contain the model's mistakes.
  gold.jsonl       -- only entries a teacher has reviewed and confirmed
                      correct (or manually corrected). THIS is what you
                      actually train on -- quality over quantity.

Each raw_log.jsonl record also gets its input file copied into
training_data/inputs/, so you (or a review UI) can look at the original
homework/audio next to the JSON the model produced when deciding whether
to promote it to gold.
"""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import TRAINING_GOLD_LOG, TRAINING_INPUTS_DIR, TRAINING_RAW_LOG


def log_raw_example(
    input_file_path: Path,
    system_prompt: str,
    user_prompt: str,
    model_output: dict,
    subject_key: str = "",
    custom_subject_name: str = "",
    language_key: str = "",
    skill_key: str = "",
    model_name: str = "",
    backend: str = "",
) -> str:
    """
    Appends one record to raw_log.jsonl and copies the input file into
    training_data/inputs/. Returns the generated example_id so callers
    (e.g. a review UI) can look the record up again later.

    Never raises -- a logging failure should not break homework grading
    for the teacher currently using the app.
    """
    example_id = uuid.uuid4().hex
    try:
        input_file_path = Path(input_file_path)
        stored_input_name = f"{example_id}{input_file_path.suffix.lower()}"
        stored_input_path = TRAINING_INPUTS_DIR / stored_input_name
        shutil.copyfile(input_file_path, stored_input_path)

        record = {
            "example_id": example_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input_file": stored_input_name,
            "original_filename": input_file_path.name,
            "backend": backend,
            "model_name": model_name,
            "subject_key": subject_key,
            "custom_subject_name": custom_subject_name,
            "language_key": language_key,
            "skill_key": skill_key,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "model_output": model_output,
            "reviewed": False,
        }

        with open(TRAINING_RAW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception:
        # Best-effort logging -- swallow errors so this never blocks
        # the actual grading flow the teacher/student is waiting on.
        pass

    return example_id


def promote_to_gold(example_id: str, corrected_output: Optional[dict] = None) -> bool:
    """
    Finds `example_id` in raw_log.jsonl and appends a (possibly teacher-
    corrected) version to gold.jsonl -- the verified dataset that's
    actually safe to fine-tune on. Returns True if found and promoted.
    """
    if not TRAINING_RAW_LOG.exists():
        return False

    with open(TRAINING_RAW_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record["example_id"] != example_id:
                continue

            gold_record = dict(record)
            gold_record["model_output"] = corrected_output or record["model_output"]
            gold_record["was_corrected"] = corrected_output is not None
            gold_record["promoted_at"] = datetime.now(timezone.utc).isoformat()

            with open(TRAINING_GOLD_LOG, "a", encoding="utf-8") as gf:
                gf.write(json.dumps(gold_record, ensure_ascii=False) + "\n")
            return True

    return False


def count_examples() -> dict:
    """Quick stats: how much data has been collected so far."""
    def _count(path: Path) -> int:
        if not path.exists():
            return 0
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    return {
        "raw": _count(TRAINING_RAW_LOG),
        "gold": _count(TRAINING_GOLD_LOG),
    }
