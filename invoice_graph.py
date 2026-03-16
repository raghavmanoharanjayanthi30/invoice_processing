"""
LangGraph invoice processing: ingest -> validate -> [optional re_extract] -> approve -> pay.
Agents: Extractor (ingest), Validator, Approver, Payer. Optional retry: re_extract with validation feedback.
State: InvoiceProcessingState (single top-level state).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from invoice_parsers import parse_invoice
from invoice_schema import (
    ApprovalResult,
    InvoiceData,
    InvoiceProcessingState,
    PaymentResult,
    ValidationResult,
    default_approval_result,
    default_payment_result,
    default_validation_result,
    raw_parser_output_to_invoice_data,
)

logger = logging.getLogger("invoice_graph")
# Default DB path (project root)
INVENTORY_DB = Path(__file__).resolve().parent / "inventory.db"


def _log(state: InvoiceProcessingState, msg: str) -> list[str]:
    logs = list(state.get("processing_logs") or [])
    logs.append(msg)
    logger.info(msg)
    return logs


# --- Agent 1: Extractor — parse file and extract structured invoice data ---
def ingest_node(state: InvoiceProcessingState) -> dict:
    path_str = state["invoice_path"]
    path = Path(path_str)
    logs = _log(state, f"[ingest] Processing path: {path_str}")

    if not path.is_absolute():
        base = Path(__file__).resolve().parent
        path = base / path_str

    file_type = path.suffix.lower() if path.suffix else None
    updates: dict = {
        "file_type": file_type,
        "processing_logs": logs,
    }

    try:
        raw = parse_invoice(path)
        invoice_data = raw_parser_output_to_invoice_data(raw)
        updates["raw_parser_output"] = raw
        updates["parser_used"] = raw.get("parser_used")
        updates["invoice_data"] = invoice_data
        updates["ingestion_status"] = "success"
        updates["overall_status"] = "ingested"
        updates["parse_errors"] = []
        updates["processing_logs"] = _log({**state, **updates}, "[ingest] Parsed successfully.")
    except Exception as e:
        updates["ingestion_status"] = "failed"
        updates["overall_status"] = "failed"
        updates["parse_errors"] = [str(e)]
        updates["processing_logs"] = _log({**state, **updates}, f"[ingest] Parse failed: {e}")

    return updates


# --- Agent 2: Validator — check line items and quantities against inventory DB ---
def validate_node(state: InvoiceProcessingState) -> dict:
    logs = _log(state, "[validate] Running validation.")
    result = default_validation_result()

    invoice_data = state.get("invoice_data")
    if not invoice_data:
        result["status"] = "not_run"
        result["issues"] = ["No invoice data to validate (ingestion failed)."]
        result["validator_notes"] = ["Skipped: no invoice_data."]
        return {
            "validation_result": result,
            "overall_status": state.get("overall_status", "ingested"),
            "processing_logs": _log(state, "[validate] Skipped (no data)."),
        }

    result["status"] = "passed"
    issues: list[str] = []
    matched: list[str] = []
    unmatched: list[str] = []
    stock_mismatches: list[str] = []
    integrity_flags: list[str] = []
    validator_notes: list[str] = []

    # Check for missing vendor, negative qty, total mismatch
    if invoice_data.get("missing_fields"):
        for f in invoice_data["missing_fields"]:
            if f in ("vendor", "invoice_number", "total_amount", "line_items"):
                integrity_flags.append(f"missing {f}")
    if invoice_data.get("suspected_fraud"):
        integrity_flags.append("suspected fraud flag set")

    line_items = invoice_data.get("line_items") or []
    for li in line_items:
        qty = li.get("quantity")
        if qty is not None and qty < 0:
            integrity_flags.append("negative quantity on line item")

    # Inventory lookup
    if not INVENTORY_DB.exists():
        validator_notes.append("inventory.db not found; skipping stock checks.")
        result["status"] = "passed"
        result["issues"] = issues
        result["matched_items"] = matched
        result["unmatched_items"] = unmatched
        result["stock_mismatches"] = stock_mismatches
        result["integrity_flags"] = integrity_flags
        result["validator_notes"] = validator_notes
        return {
            "validation_result": result,
            "overall_status": "validated",
            "processing_logs": _log(state, "[validate] Passed (no DB)."),
        }

    try:
        conn = sqlite3.connect(INVENTORY_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT item, stock FROM inventory")
        inventory = {row["item"]: row["stock"] for row in cur.fetchall()}
        conn.close()
    except Exception as e:
        validator_notes.append(f"DB error: {e}")
        result["status"] = "failed"
        result["issues"] = [str(e)]
        result["validator_notes"] = validator_notes
        return {
            "validation_result": result,
            "overall_status": "failed",
            "processing_logs": _log(state, f"[validate] DB error: {e}"),
        }

    for li in line_items:
        item_name = (li.get("item_name") or "").strip()
        if not item_name:
            continue
        qty = li.get("quantity") or 0
        stock = inventory.get(item_name)
        if stock is None:
            unmatched.append(item_name)
            issues.append(f"Item {item_name!r} not found in inventory.")
        else:
            matched.append(item_name)
            if qty > stock:
                stock_mismatches.append(f"{item_name}: requested {qty}, stock {stock}")
                issues.append(f"Requested quantity {qty} exceeds stock {stock} for {item_name}")

    if issues or integrity_flags:
        result["status"] = "failed"
    result["issues"] = issues
    result["matched_items"] = matched
    result["unmatched_items"] = unmatched
    result["stock_mismatches"] = stock_mismatches
    result["integrity_flags"] = integrity_flags
    result["validator_notes"] = validator_notes

    return {
        "validation_result": result,
        "overall_status": "validated",
        "processing_logs": _log(state, f"[validate] {result['status']}."),
    }


# --- Re-extract with validation feedback (one retry for LLM-extracted TXT/PDF) ---
def re_extract_node(state: InvoiceProcessingState) -> dict:
    raw = state.get("raw_parser_output") or {}
    raw_text = raw.get("raw_text") or ""
    validation = state.get("validation_result") or default_validation_result()
    issues = validation.get("issues") or []
    retry_count = state.get("extraction_retry_count") or 0

    _log(state, "[re_extract] Retrying extraction with validation feedback.")
    try:
        from llm_extract import extract_invoice_from_text
        llm_result = extract_invoice_from_text(raw_text, validation_feedback=issues)
        if llm_result is not None:
            inv_data = raw_parser_output_to_invoice_data(llm_result)
            return {
                "invoice_data": inv_data,
                "raw_parser_output": llm_result,
                "parser_used": (state.get("parser_used") or "txt").replace("+llm", "") + "+llm+retry",
                "extraction_retry_count": retry_count + 1,
                "processing_logs": _log(state, "[re_extract] Re-extraction done."),
            }
    except Exception as e:
        logger.warning("Re-extract failed: %s", e)
    return {
        "extraction_retry_count": retry_count + 1,
        "processing_logs": _log(state, "[re_extract] Re-extraction failed or skipped."),
    }


# --- Agent 3: Approver — rule-based approve/reject/manual_review ---
def approve_node(state: InvoiceProcessingState) -> dict:
    logs = _log(state, "[approve] Running approval.")
    result: ApprovalResult = default_approval_result()

    validation = state.get("validation_result") or default_validation_result()
    invoice_data = state.get("invoice_data")

    if validation.get("status") == "failed":
        result["status"] = "rejected"
        result["reasons"] = list(validation.get("issues") or [])
        result["approval_notes"] = ["Rejected due to validation failures."]
        return {
            "approval_result": result,
            "overall_status": "rejected",
            "processing_logs": _log(state, "[approve] Rejected (validation failed)."),
        }

    if not invoice_data:
        result["status"] = "rejected"
        result["reasons"] = ["No invoice data."]
        return {
            "approval_result": result,
            "overall_status": "rejected",
            "processing_logs": _log(state, "[approve] Rejected (no data)."),
        }

    total = invoice_data.get("total_amount") or 0
    suspected_fraud = invoice_data.get("suspected_fraud") or False
    reasons: list[str] = []
    approval_notes: list[str] = []
    reflection_notes: list[str] = []

    if suspected_fraud:
        result["status"] = "manual_review"
        result["requires_additional_scrutiny"] = True
        reasons.append("Suspected fraud flag set.")
        reflection_notes.append("Invoice marked as suspected fraud; escalate to manual review.")
    elif total and float(total) > 10_000:
        result["requires_additional_scrutiny"] = True
        approval_notes.append("Amount over $10K; additional scrutiny applied.")
        result["status"] = "approved"
        reasons.append("Approved with scrutiny.")
    else:
        result["status"] = "approved"
        reasons.append("Validation passed; amount within policy.")

    result["reasons"] = reasons
    result["approval_notes"] = approval_notes
    result["reflection_notes"] = reflection_notes

    return {
        "approval_result": result,
        "overall_status": "approved" if result["status"] == "approved" else result["status"],
        "processing_logs": _log(state, f"[approve] {result['status']}."),
    }


# --- Agent 4: Payer — call mock payment if approved ---
def pay_node(state: InvoiceProcessingState) -> dict:
    logs = _log(state, "[pay] Running payment.")
    result: PaymentResult = default_payment_result()

    approval = state.get("approval_result") or default_approval_result()
    status = approval.get("status", "not_run")

    if status not in ("approved",):
        result["status"] = "skipped"
        result["payment_notes"] = [f"Approval status was {status}; payment skipped."]
        return {
            "payment_result": result,
            "overall_status": state.get("overall_status", "rejected"),
            "processing_logs": _log(state, "[pay] Skipped."),
        }

    invoice_data = state.get("invoice_data")
    vendor = (invoice_data or {}).get("vendor") or "Unknown"
    amount = (invoice_data or {}).get("total_amount") or 0

    try:
        # Mock payment (README)
        out = mock_payment(vendor, amount)
        result["status"] = "success"
        result["transaction_id"] = out.get("transaction_id", "mock-txn-001")
        result["payment_notes"] = [f"Paid {amount} to {vendor}"]
        return {
            "payment_result": result,
            "overall_status": "paid",
            "processing_logs": _log(state, "[pay] Success."),
        }
    except Exception as e:
        result["status"] = "failed"
        result["failure_reasons"] = [str(e)]
        return {
            "payment_result": result,
            "overall_status": "failed",
            "processing_logs": _log(state, f"[pay] Failed: {e}"),
        }


def mock_payment(vendor: str, amount: float) -> dict:
    print(f"Paid {amount} to {vendor}")
    return {"status": "success", "transaction_id": "mock-txn-001"}


def _after_validate(state: InvoiceProcessingState) -> str:
    """If validation failed and we can retry (LLM source, one retry), go to re_extract; else approve."""
    vr = state.get("validation_result") or {}
    if vr.get("status") != "failed":
        return "approve"
    raw = state.get("raw_parser_output") or {}
    parser_used = state.get("parser_used") or ""
    retry_count = state.get("extraction_retry_count") or 0
    if raw.get("raw_text") and "+llm" in parser_used and retry_count < 1:
        return "re_extract"
    return "approve"


# --- Conditional: after approve, go to pay or END ---
def after_approve(state: InvoiceProcessingState) -> str:
    approval = state.get("approval_result") or {}
    if approval.get("status") == "approved":
        return "pay"
    return "end"


# --- Build graph ---
def build_graph():
    workflow = StateGraph(InvoiceProcessingState)

    workflow.add_node("ingest", ingest_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("re_extract", re_extract_node)
    workflow.add_node("approve", approve_node)
    workflow.add_node("pay", pay_node)

    workflow.add_edge(START, "ingest")
    workflow.add_edge("ingest", "validate")
    workflow.add_conditional_edges("validate", _after_validate, {"approve": "approve", "re_extract": "re_extract"})
    workflow.add_edge("re_extract", "validate")
    workflow.add_conditional_edges("approve", after_approve, {"pay": "pay", "end": END})
    workflow.add_edge("pay", END)

    return workflow.compile()


# For CLI
def run_graph(invoice_path: str) -> InvoiceProcessingState:
    from invoice_schema import initial_state

    graph = build_graph()
    initial = initial_state(invoice_path)
    final = graph.invoke(initial)
    return final
