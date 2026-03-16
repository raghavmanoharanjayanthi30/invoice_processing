"""Unit tests for invoice_graph."""
from pathlib import Path

import pytest

from invoice_graph import build_graph, run_graph
from invoice_schema import initial_state


def test_build_graph():
    g = build_graph()
    assert g is not None


def test_initial_state_keys():
    s = initial_state("data/invoices/invoice_1004.json")
    assert "invoice_path" in s
    assert "validation_result" in s
    assert "approval_result" in s
    assert "payment_result" in s
    assert "processing_logs" in s


def test_run_graph_with_project_invoice():
    """Run pipeline on a real invoice in the project (requires inventory.db)."""
    root = Path(__file__).resolve().parent.parent
    inv_path = root / "data" / "invoices" / "invoice_1004.json"
    if not inv_path.exists():
        pytest.skip("data/invoices/invoice_1004.json not found")
    state = run_graph("data/invoices/invoice_1004.json")
    assert state["ingestion_status"] == "success"
    assert state["invoice_data"] is not None
    assert state["overall_status"] in ("paid", "approved", "validated", "rejected", "failed")
    assert "processing_logs" in state
