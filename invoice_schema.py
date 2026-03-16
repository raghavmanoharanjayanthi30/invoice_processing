"""
Invoice processing schema for LangGraph state.
All types are TypedDict for JSON-serializable, merge-friendly state.
"""
from __future__ import annotations

from typing import TypedDict


# --- 1. InvoiceLineItem ---
class InvoiceLineItem(TypedDict, total=False):
    item_name: str | None
    quantity: int | None
    unit_price: float | None
    line_total: float | None
    notes: list[str]


# --- 2. InvoiceData ---
class InvoiceData(TypedDict, total=False):
    vendor: str | None
    invoice_number: str | None
    invoice_date: str | None
    due_date: str | None
    subtotal: float | None
    tax_amount: float | None
    total_amount: float | None
    payment_terms: str | None
    invoice_notes: str | None
    payment_currency: str | None
    line_items: list[InvoiceLineItem]
    missing_fields: list[str]
    warnings: list[str]
    suspected_fraud: bool
    extraction_confidence: float | None


# --- 3. ValidationResult ---
class ValidationResult(TypedDict, total=False):
    status: str  # "not_run" | "passed" | "failed"
    issues: list[str]
    matched_items: list[str]
    unmatched_items: list[str]
    stock_mismatches: list[str]
    integrity_flags: list[str]
    validator_notes: list[str]


# --- 4. ApprovalResult ---
class ApprovalResult(TypedDict, total=False):
    status: str  # "not_run" | "approved" | "rejected" | "manual_review"
    reasons: list[str]
    requires_additional_scrutiny: bool
    approval_notes: list[str]
    reflection_notes: list[str]


# --- 5. PaymentResult ---
class PaymentResult(TypedDict, total=False):
    status: str  # "not_run" | "success" | "failed" | "skipped"
    transaction_id: str | None
    failure_reasons: list[str]
    payment_notes: list[str]


# --- 6. InvoiceProcessingState (top-level LangGraph state) ---
class InvoiceProcessingState(TypedDict, total=False):
    invoice_path: str
    file_type: str | None
    raw_parser_output: dict | None
    parser_used: str | None
    parse_errors: list[str]
    invoice_data: InvoiceData | None
    ingestion_status: str  # "not_run" | "success" | "failed"
    validation_result: ValidationResult
    approval_result: ApprovalResult
    payment_result: PaymentResult
    overall_status: str  # "pending" | "ingested" | "validated" | "approved" | "rejected" | "paid" | "failed"
    processing_logs: list[str]
    extraction_retry_count: int  # for validation retry loop (max 1)


# --- Default result builders ---
def default_validation_result() -> ValidationResult:
    return ValidationResult(
        status="not_run",
        issues=[],
        matched_items=[],
        unmatched_items=[],
        stock_mismatches=[],
        integrity_flags=[],
        validator_notes=[],
    )


def default_approval_result() -> ApprovalResult:
    return ApprovalResult(
        status="not_run",
        reasons=[],
        requires_additional_scrutiny=False,
        approval_notes=[],
        reflection_notes=[],
    )


def default_payment_result() -> PaymentResult:
    return PaymentResult(
        status="not_run",
        transaction_id=None,
        failure_reasons=[],
        payment_notes=[],
    )


def initial_state(invoice_path: str) -> InvoiceProcessingState:
    """Build initial state for the graph (invoice_path set; rest defaults)."""
    return InvoiceProcessingState(
        invoice_path=invoice_path,
        file_type=None,
        raw_parser_output=None,
        parser_used=None,
        parse_errors=[],
        invoice_data=None,
        ingestion_status="not_run",
        validation_result=default_validation_result(),
        approval_result=default_approval_result(),
        payment_result=default_payment_result(),
        overall_status="pending",
        processing_logs=[],
        extraction_retry_count=0,
    )


# --- Map raw parser output (invoice_parsers) -> InvoiceData ---
def raw_parser_output_to_invoice_data(raw: dict) -> InvoiceData:
    """Convert dict from invoice_parsers.parse_invoice() to InvoiceData."""
    line_items: list[InvoiceLineItem] = []
    for li in raw.get("line_items") or []:
        item_name = li.get("item") or li.get("name") or None
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
        line_total = None
        if qty is not None and up is not None:
            line_total = qty * up
        notes: list[str] = []
        if qty is not None and qty < 0:
            notes.append("negative quantity")
        if up is None and (li.get("unit_price") is not None or "unit_price" in li):
            notes.append("unit price missing or invalid")
        line_items.append(InvoiceLineItem(
            item_name=item_name,
            quantity=qty,
            unit_price=up,
            line_total=line_total,
            notes=notes,
        ))

    raw_text = (raw.get("raw_text") or "") if isinstance(raw.get("raw_text"), str) else ""
    payment_currency = "USD"
    if raw_text and ("$" in raw_text or "dollar" in raw_text.lower()):
        payment_currency = "USD"
    if raw_text and ("eur" in raw_text.lower() or "€" in raw_text):
        payment_currency = "EUR"

    missing: list[str] = []
    if not raw.get("vendor"):
        missing.append("vendor")
    if not raw.get("invoice_number"):
        missing.append("invoice_number")
    if not raw.get("date"):
        missing.append("invoice_date")
    if not raw.get("due_date"):
        missing.append("due_date")
    if raw.get("total") is None and raw.get("total_amount") is None:
        missing.append("total_amount")
    if not line_items:
        missing.append("line_items")

    warnings: list[str] = []
    if raw.get("parser_used") in ("txt", "pdf_pdfplumber", "pdf_pymupdf") and not line_items:
        warnings.append("no structured line items extracted; consider LLM extraction")

    suspected_fraud = False
    if raw_text:
        lower = raw_text.lower()
        if "urgent" in lower and "wire" in lower:
            warnings.append("urgent wire transfer language")
        if "fake" in lower or "fraud" in lower or "immediate" in lower and "penalties" in lower:
            suspected_fraud = True

    total_amount = raw.get("total")
    if total_amount is not None and not isinstance(total_amount, (int, float)):
        try:
            total_amount = float(total_amount)
        except (TypeError, ValueError):
            total_amount = None

    subtotal = raw.get("subtotal")
    if subtotal is not None and not isinstance(subtotal, (int, float)):
        try:
            subtotal = float(subtotal)
        except (TypeError, ValueError):
            subtotal = None

    tax_amount = raw.get("tax_amount") or raw.get("tax")
    if tax_amount is not None and not isinstance(tax_amount, (int, float)):
        try:
            tax_amount = float(tax_amount)
        except (TypeError, ValueError):
            tax_amount = None

    return InvoiceData(
        vendor=raw.get("vendor"),
        invoice_number=raw.get("invoice_number"),
        invoice_date=raw.get("date"),
        due_date=raw.get("due_date"),
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        payment_terms=None,
        invoice_notes=raw_text[:2000] if raw_text else None,
        payment_currency=payment_currency,
        line_items=line_items,
        missing_fields=missing,
        warnings=warnings,
        suspected_fraud=suspected_fraud,
        extraction_confidence=raw.get("extraction_confidence"),
    )
