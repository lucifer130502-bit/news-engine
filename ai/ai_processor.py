from __future__ import annotations

import json
import re
import logging
from config.config_context import config
from openai import OpenAI

logger = logging.getLogger(__name__)

# Initialize LLM client - using LLM gateway only
_client = OpenAI(
    base_url=config.llm_gateway.base_url,
    api_key=config.llm_gateway.api_key
)
_model = config.app.llm_model

CLASSIFICATION_PROMPT = """

Classify the announcement using ONLY these labels and definitions:
{label_definitions}

If the announcement fits multiple labels, return all applicable labels in classification_label ordered by this priority order:
{priority}

Never invent a new label. If nothing clearly fits, use "Other Announcement".
"""

USER_PROMPT_STRUCTURE = """

Announcement Metadata:
- Company: {company}
- Exchange: {exchange}
- Announcement Type: {announcement_type}
- Date: {date}

PDF Content (first 8000 chars):
{pdf_text}

Return ONLY a valid JSON object with this exact structure (no markdown, no explanation):
{{
  "summary": "<string — follow the instruction above>",
  "sentiment": "Positive" | "Neutral" | "Negative",
  "sentiment_score": <float 0.0 to 1.0 where 1.0 is most positive>,
  "relevance": "High" | "Medium" | "Low",
  "key_points": ["point 1", "point 2", "point 3"],
  "announcement_type": "detected type if not already known, e.g. Dividend, Result, AGM, Merger, Buyback, etc.",
  "classification_label": ["one or more labels from the approved list, ordered by priority with the most important first"]
}}
"""


def _normalize_classification_label(value, valid_label_set: set[str]) -> list[str]:
    if isinstance(value, str):
        raw_labels = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        raw_labels = [str(part).strip() for part in value]
    else:
        raw_labels = []

    result = []
    seen = set()
    for label in raw_labels:
        if label in valid_label_set and label not in seen:
            result.append(label)
            seen.add(label)

    return result


def resolve_classification(
    result: dict,
    valid_label_names: list[str],
    priority_rank: dict[str, int],
) -> dict:
    labels = _normalize_classification_label(
        result.get("classification_label"), set(valid_label_names)
    )

    if not labels:
        labels = ["Other Announcement"]

    ordered_labels = sorted(
        labels,
        key=lambda label: priority_rank.get(label, len(priority_rank)),
    )

    result["classification_label"] = ordered_labels
    return result


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(__import__("io").BytesIO(pdf_bytes)) as pdf:
            text = ""
            for page in pdf.pages[:5]:  # first 5 pages max
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            return text[:8000]
    except Exception as e:
        logger.error(f"PDF text extraction failed: {e}")
        return ""


def process_announcement(
    pdf_bytes: bytes | None,
    metadata: dict,
    fallback_text: str = "",
    custom_instruction: str | None = None,
    labels: list[dict] | None = None,
    label_priority: list[str] | None = None,
) -> dict:
    """Process an announcement through the LLM.

    labels and label_priority are fetched from the ai_prompts table in the DB
    (via db.get_prompt()) and passed in by the caller. Both must be provided;
    if the DB row has no label config, processing will fall back to a minimal
    error result rather than silently using stale hardcoded values.
    """
    if not labels or not label_priority:
        logger.error("process_announcement called without labels/label_priority from DB — check ai_prompts table")
        return {
            "summary": "Summary not available.",
            "sentiment": "Neutral",
            "sentiment_score": 0.5,
            "relevance": "Medium",
            "key_points": [],
            "announcement_type": metadata.get("announcement_type", "Unknown"),
            "classification_label": ["Other Announcement"],
        }

    if pdf_bytes:
        pdf_text = extract_text_from_pdf(pdf_bytes)
    else:
        pdf_text = ""

    # Use fallback_text (API details field) when PDF is unavailable
    content = pdf_text or fallback_text or "No content available."

    label_names = [l["name"] for l in labels]
    explanations = {l["name"]: l["explanation"] for l in labels}
    priority_rank = {name: i for i, name in enumerate(label_priority)}

    label_definitions = "\n".join(
        f"- {name}: {explanations[name]}" for name in label_names if name in explanations
    )

    instruction = custom_instruction or "You are a financial analyst assistant. Analyze the following corporate announcement and return a JSON object."
    system_prompt = instruction + CLASSIFICATION_PROMPT.format(
        label_definitions=label_definitions,
        priority=" > ".join(label_priority),
    )
    user_prompt = USER_PROMPT_STRUCTURE.format(
        company=metadata.get("company", "Unknown"),
        exchange=metadata.get("exchange", "Unknown"),
        announcement_type=metadata.get("announcement_type", "Unknown"),
        date=metadata.get("date", "Unknown"),
        pdf_text=content,
    )

    try:
        logger.info(f"LLM Gateway Request - Model: {_model}, Company: {metadata.get('company', 'Unknown')}, Exchange: {metadata.get('exchange', 'Unknown')}, Type: {metadata.get('announcement_type', 'Unknown')}, Temperature: 0.3")
        logger.debug(f"LLM Gateway System Prompt (first 500 chars): {system_prompt[:500]}...")
        logger.debug(f"LLM Gateway User Prompt (first 500 chars): {user_prompt[:500]}...")

        response = _client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3
        )
        raw = response.choices[0].message.content.strip()

        logger.info(f"LLM Gateway Response - Company: {metadata.get('company', 'Unknown')}, Response length: {len(raw)} chars")

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        result = resolve_classification(result, label_names, priority_rank)
    except Exception as e:
        logger.error(f"LLM processing failed: {e}")
        result = {
            "summary": "Summary not available.",
            "sentiment": "Negative",
            "sentiment_score": 0.0,
            "relevance": "Medium",
            "key_points": [],
            "announcement_type": metadata.get("announcement_type", "Unknown"),
            "classification_label": ["Other Announcement"],
        }

    return result
