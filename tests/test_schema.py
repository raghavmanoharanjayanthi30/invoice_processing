"""Unit tests for invoice_schema."""
import pytest
from invoice_schema import (
    initial_state,
    raw_parser_output_to_invoice_data,
    default_validation_result,
    default_approval_result,
    default_payment_result,
)


def test_initial_state():
    s = initial_state("data/invoices/invoice_1004.json")
    assert s["invoice_path"] == "data/invoices/invoice_1004.json"
    assert s["overall_status"] == "pending"
    assert s["ingestion_status"] == "not_run"
    assert s.get("extraction_retry_count", 0) == 0
    assert s["validation_result"]["status"] == "not_run"
    assert s["approval_result"]["status"] == "not_run"
    assert s["payment_result"]["status"] == "not_run"


def test_raw_parser_output_to_invoice_data():
    raw = {
        "vendor": "Acme",
        "invoice_number": "INV-1",
        "date": "2026-01-01",
        "due_date": "2026-02-01",
        "line_items": [
            {"item": "WidgetA", "quantity": 2, "unit_price": 100.0},
        ],
        "total": 200.0,
        "subtotal": 200.0,
        "tax_amount": None,
    }
    inv = raw_parser_output_to_invoice_data(raw)
    assert inv["vendor"] == "Acme"
    assert inv["invoice_number"] == "INV-1"
    assert inv["invoice_date"] == "2026-01-01"
    assert inv["due_date"] == "2026-02-01"
    assert inv["total_amount"] == 200.0
    assert len(inv["line_items"]) == 1
    assert inv["line_items"][0]["item_name"] == "WidgetA"
    assert inv["line_items"][0]["quantity"] == 2
    assert inv["line_items"][0]["unit_price"] == 100.0
    assert "line_items" not in inv["missing_fields"]


def test_raw_parser_output_missing_fields():
    raw = {"vendor": None, "invoice_number": None, "line_items": [], "total": None}
    inv = raw_parser_output_to_invoice_data(raw)
    assert "vendor" in inv["missing_fields"]
    assert "line_items" in inv["missing_fields"]
    assert inv["line_items"] == []


def test_default_results():
    vr = default_validation_result()
    assert vr["status"] == "not_run"
    assert vr["issues"] == []
    ar = default_approval_result()
    assert ar["status"] == "not_run"
    pr = default_payment_result()
    assert pr["status"] == "not_run"
