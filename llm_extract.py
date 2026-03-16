"""
LLM-based extraction of structured invoice data from raw text (TXT/PDF).
Uses OpenAI API; key from env (export OPENAI_API_KEY=sk-...) or .env. Falls back to None if key missing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Load .env from project root when this module is imported
def _load_dotenv():
    try:
        from dotenv import load_dotenv
        root = Path(__file__).resolve().parent
        load_dotenv(root / ".env")
        load_dotenv(root / ".env.local")
    except ImportError:
        pass


_load_dotenv()

EXTRACT_SCHEMA = """
Return a single JSON object with exactly these keys (use null for missing):
- vendor (string): company or person that issued the invoice
- invoice_number (string): e.g. INV-1008
- date (string): invoice date
- due_date (string): payment due date
- line_items (array): each element has { "item": string, "quantity": integer, "unit_price": number }
- total (number): total amount, no currency symbol
- subtotal (number or null)
- tax_amount (number or null)
- payment_terms (string or null): e.g. "Net 30"
- extraction_confidence (number 0-1 or null): your confidence in this extraction, e.g. 0.95
Extract from the text; infer dates in YYYY-MM-DD when possible. For line items use item name, quantity, and unit price. Return only the JSON object, no markdown or explanation.
"""


def extract_invoice_from_text(raw_text: str, validation_feedback: list[str] | None = None) -> dict | None:
    """
    Use OpenAI to extract structured invoice fields from raw text.
    If validation_feedback is provided (e.g. from a failed validation), the LLM is asked to re-extract and fix.
    Returns a dict in parser format or None if API key is missing or the request fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY".lower())
    if not api_key or not api_key.strip() or api_key.startswith("sk-your-"):
        return None

    raw_text = (raw_text or "").strip()
    if not raw_text:
        return None

    user_content = "Extract invoice data from this text:\n\n" + raw_text[:12000]
    if validation_feedback:
        user_content = (
            "Validation previously failed with these issues. Re-extract and fix the data where possible.\n\n"
            "Validation issues:\n" + "\n".join(f"- {s}" for s in validation_feedback) + "\n\n"
            "Original text:\n\n" + raw_text[:10000]
        )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You extract invoice data from text. " + EXTRACT_SCHEMA},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return None
        data = json.loads(content)
    except Exception:
        return None

    # Normalize to our parser format
    line_items = []
    for li in data.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        item = li.get("item") or li.get("name")
        qty = li.get("quantity") or li.get("qty")
        if qty is not None and not isinstance(qty, int):
            try:
                qty = int(float(qty))
            except (TypeError, ValueError):
                qty = None
        up = li.get("unit_price")
        if up is not None and not isinstance(up, (int, float)):
            try:
                up = float(up)
            except (TypeError, ValueError):
                up = None
        line_items.append({"item": item, "quantity": qty, "unit_price": up})

    total = data.get("total")
    if total is not None and not isinstance(total, (int, float)):
        try:
            total = float(total)
        except (TypeError, ValueError):
            total = None

    subtotal = data.get("subtotal")
    if subtotal is not None and not isinstance(subtotal, (int, float)):
        try:
            subtotal = float(subtotal)
        except (TypeError, ValueError):
            subtotal = None

    tax_amount = data.get("tax_amount")
    if tax_amount is not None and not isinstance(tax_amount, (int, float)):
        try:
            tax_amount = float(tax_amount)
        except (TypeError, ValueError):
            tax_amount = None

    confidence = data.get("extraction_confidence")
    if confidence is not None and not isinstance(confidence, (int, float)):
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None
    if confidence is not None and (confidence < 0 or confidence > 1):
        confidence = None

    return {
        "vendor": data.get("vendor"),
        "invoice_number": data.get("invoice_number"),
        "date": data.get("date"),
        "due_date": data.get("due_date"),
        "line_items": line_items,
        "total": total,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "payment_terms": data.get("payment_terms"),
        "extraction_confidence": confidence,
        "raw_text": raw_text,
        "parser_used": "llm_openai",
    }
