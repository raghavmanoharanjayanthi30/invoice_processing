#!/usr/bin/env python3
"""
Run invoice processing pipeline from CLI.
Usage: python main.py --invoice_path=data/invoices/invoice_1004.json

Exit codes (for scripting):
  0 - Pipeline ran successfully (invoice paid, approved, or completed; no crash).
  1 - Error or rejected: missing/invalid args, ingestion failed, or invoice rejected by validation/approval.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

# Centralized logging for pipeline (invoice_graph uses logger "invoice_graph")
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

from invoice_graph import run_graph
from invoice_schema import InvoiceProcessingState

# ANSI colors (disabled when not a TTY or --no-color)
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _color(s: str, color: str, use_color: bool) -> str:
    return f"{color}{s}{RESET}" if use_color else s


def _serialize_state(state: InvoiceProcessingState) -> dict:
    """Convert state to JSON-serializable dict (for logs/output)."""
    out = dict(state)
    # Ensure nested dicts and lists are plain dicts/lists
    for key in ("invoice_data", "validation_result", "approval_result", "payment_result"):
        if key in out and out[key] is not None:
            out[key] = dict(out[key])
    if out.get("invoice_data") and "line_items" in out["invoice_data"]:
        out["invoice_data"]["line_items"] = [dict(li) for li in out["invoice_data"]["line_items"]]
    return out


def main():
    parser = argparse.ArgumentParser(description="Process an invoice through ingest -> validate -> approve -> pay.")
    parser.add_argument(
        "--invoice_path",
        type=str,
        required=True,
        help="Path to invoice file (e.g. data/invoices/invoice_1004.json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print final state as JSON to stdout",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only one-line result summary",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    args = parser.parse_args()

    invoice_path = args.invoice_path.strip()
    if not invoice_path:
        print("Error: --invoice_path is required.")
        return 1

    use_color = not args.no_color and sys.stdout.isatty()

    try:
        final_state = run_graph(invoice_path)
    except Exception as e:
        print(f"Pipeline error: {e}")
        return 1

    status = final_state.get("overall_status", "unknown")
    # Exit 1 if rejected or failed (for scripting)
    if status in ("rejected", "failed"):
        exit_code = 1
    else:
        exit_code = 0

    if args.quiet:
        if status == "paid":
            print(_color(f"Result: Paid — {invoice_path}", GREEN, use_color))
        elif status == "approved":
            print(_color(f"Result: Approved — {invoice_path}", GREEN, use_color))
        elif status in ("rejected", "failed"):
            print(_color(f"Result: {status.capitalize()} — {invoice_path}", RED, use_color))
        else:
            print(f"Result: {status} — {invoice_path}")
        return exit_code

    print(f"Processing invoice: {invoice_path}")
    print("---")

    # Structured logs
    for log in final_state.get("processing_logs") or []:
        print(log)

    print("---")
    status_label = f"Overall status: {status}"
    if status == "paid":
        print(_color(status_label, GREEN, use_color))
    elif status in ("rejected", "failed"):
        print(_color(status_label, RED, use_color))
    else:
        print(_color(status_label, YELLOW, use_color))
    print()

    # Ingestion: status + reasons if failed + ingested data summary
    ing_status = final_state.get("ingestion_status")
    print(f"Ingestion: {ing_status} (parser: {final_state.get('parser_used')})")
    if final_state.get("parse_errors"):
        for err in final_state["parse_errors"]:
            print(f"  └ parse error: {err}")
    # Print what was actually ingested (so user sees why validation may fail)
    inv = final_state.get("invoice_data")
    if inv is not None:
        print("  Ingested data:")
        print(f"    vendor: {inv.get('vendor')}")
        print(f"    invoice_number: {inv.get('invoice_number')}")
        print(f"    invoice_date: {inv.get('invoice_date')}")
        print(f"    due_date: {inv.get('due_date')}")
        if inv.get("subtotal") is not None:
            print(f"    subtotal: {inv.get('subtotal')}")
        if inv.get("tax_amount") is not None:
            print(f"    tax_amount: {inv.get('tax_amount')}")
        print(f"    total_amount: {inv.get('total_amount')}")
        line_items = inv.get("line_items") or []
        print(f"    line_items: {len(line_items)}")
        for li in line_items[:10]:
            name = li.get("item_name") or li.get("item")
            qty = li.get("quantity")
            up = li.get("unit_price")
            print(f"      - {name}: qty={qty}, unit_price={up}")
        if len(line_items) > 10:
            print(f"      ... and {len(line_items) - 10} more")
        if inv.get("missing_fields"):
            print(f"    missing_fields: {inv.get('missing_fields')}")
        if inv.get("warnings"):
            print(f"    warnings: {inv.get('warnings')}")
        if inv.get("extraction_confidence") is not None:
            print(f"    extraction_confidence: {inv.get('extraction_confidence')}")
    elif final_state.get("raw_parser_output"):
        raw = final_state["raw_parser_output"]
        print("  Ingested data (from parser):")
        print(f"    vendor: {raw.get('vendor')}")
        print(f"    invoice_number: {raw.get('invoice_number')}")
        print(f"    date: {raw.get('date')}")
        print(f"    due_date: {raw.get('due_date')}")
        if raw.get("subtotal") is not None:
            print(f"    subtotal: {raw.get('subtotal')}")
        if raw.get("tax_amount") is not None:
            print(f"    tax_amount: {raw.get('tax_amount')}")
        print(f"    total: {raw.get('total')}")
        line_items = raw.get("line_items") or []
        print(f"    line_items: {len(line_items)}")
        for li in line_items[:10]:
            print(f"      - {li.get('item') or li.get('name')}: qty={li.get('quantity')}, unit_price={li.get('unit_price')}")
    print()

    # Validation: status + issues, unmatched, stock_mismatches, integrity_flags
    if final_state.get("validation_result"):
        vr = final_state["validation_result"]
        print(f"Validation: {vr.get('status')}")
        for issue in vr.get("issues") or []:
            print(f"  └ {issue}")
        for item in vr.get("unmatched_items") or []:
            print(f"  └ Item not in inventory: {item}")
        for sm in vr.get("stock_mismatches") or []:
            print(f"  └ Stock: {sm}")
        for flag in vr.get("integrity_flags") or []:
            print(f"  └ Integrity: {flag}")
        for note in vr.get("validator_notes") or []:
            print(f"  └ Note: {note}")
    print()

    # Approval: status + reasons, notes
    if final_state.get("approval_result"):
        ar = final_state["approval_result"]
        print(f"Approval: {ar.get('status')}")
        for reason in ar.get("reasons") or []:
            print(f"  └ {reason}")
        for note in ar.get("approval_notes") or []:
            print(f"  └ {note}")
        for ref in ar.get("reflection_notes") or []:
            print(f"  └ Reflection: {ref}")
    print()

    # Payment: status + failure_reasons or transaction_id
    if final_state.get("payment_result"):
        pr = final_state["payment_result"]
        print(f"Payment: {pr.get('status')}" + (f" (txn: {pr.get('transaction_id')})" if pr.get("transaction_id") else ""))
        for reason in pr.get("failure_reasons") or []:
            print(f"  └ failure: {reason}")
        for note in pr.get("payment_notes") or []:
            if note and "Paid" not in str(note):  # avoid duplicating mock "Paid X to Y"
                print(f"  └ {note}")

    # Suggested next steps when rejected/failed
    suggested = _suggested_next_steps(final_state)
    if suggested:
        print("Suggested next steps:")
        for step in suggested:
            print(f"  └ {step}")
        print()

    if args.json:
        print("---")
        print(json.dumps(_serialize_state(final_state), indent=2, default=str))

    return exit_code


def _suggested_next_steps(state: InvoiceProcessingState) -> list[str]:
    """Build 1–2 suggested next steps when invoice is rejected or failed."""
    steps = []
    status = state.get("overall_status")
    if status not in ("rejected", "failed"):
        return steps
    vr = state.get("validation_result") or {}
    issues = vr.get("issues") or []
    unmatched = vr.get("unmatched_items") or []
    if unmatched:
        steps.append(f"Add {', '.join(unmatched)} to inventory (setup_inventory.py or DB), or contact vendor to confirm product codes.")
    if vr.get("integrity_flags"):
        steps.append("Fix missing or invalid fields in the source document, or re-run with OPENAI_API_KEY set for TXT/PDF.")
    if not steps:
        steps.append("Review validation issues and approval reasons above; fix source data or inventory and retry.")
    return steps[:3]


if __name__ == "__main__":
    exit(main())
