"""Unit tests for invoice_parsers."""
import tempfile
from pathlib import Path

import pytest

from invoice_parsers import (
    parse_json,
    parse_csv_row_based,
    parse_csv_key_value,
    parse_xml,
    parse_invoice,
)


def test_parse_json(tmp_path):
    path = tmp_path / "inv.json"
    path.write_text("""
    {"vendor": "Test Co", "invoice_number": "INV-99", "date": "2026-01-01",
     "due_date": "2026-02-01", "line_items": [{"item": "A", "quantity": 1, "unit_price": 10}],
     "total": 10}
    """)
    out = parse_json(path)
    assert out["vendor"] == "Test Co"
    assert out["invoice_number"] == "INV-99"
    assert out["total"] == 10
    assert len(out["line_items"]) == 1
    assert out["line_items"][0]["item"] == "A"


def test_parse_csv_row_based(tmp_path):
    path = tmp_path / "inv.csv"
    path.write_text("""Invoice Number,Vendor,Date,Due Date,Item,Qty,Unit Price,Line Total
INV-1,Vendor A,2026-01-01,2026-02-01,WidgetA,5,100.00,500.00""")
    out = parse_csv_row_based(path)
    assert out["vendor"] == "Vendor A"
    assert out["invoice_number"] == "INV-1"
    assert len(out["line_items"]) == 1
    assert out["line_items"][0]["item"] == "WidgetA"
    assert out["line_items"][0]["quantity"] == 5


def test_parse_csv_key_value(tmp_path):
    path = tmp_path / "kv.csv"
    path.write_text("""field,value
invoice_number,INV-2
vendor,Vendor B
date,2026-01-01
due_date,2026-02-01
item,WidgetX
quantity,3
unit_price,50.00
total,150.00
payment_terms,Net 30""")
    out = parse_csv_key_value(path)
    assert out["vendor"] == "Vendor B"
    assert out["invoice_number"] == "INV-2"
    assert out["total"] == 150.0
    assert len(out["line_items"]) == 1
    assert out["line_items"][0]["item"] == "WidgetX"


def test_parse_xml(tmp_path):
    path = tmp_path / "inv.xml"
    path.write_text("""<?xml version="1.0"?>
    <invoice>
      <header>
        <invoice_number>INV-3</invoice_number>
        <vendor>Vendor C</vendor>
        <date>2026-01-01</date>
        <due_date>2026-02-01</due_date>
      </header>
      <line_items>
        <item><name>GadgetY</name><quantity>2</quantity><unit_price>25.00</unit_price></item>
      </line_items>
      <totals><subtotal>50</subtotal><total>50</total></totals>
    </invoice>""")
    out = parse_xml(path)
    assert out["vendor"] == "Vendor C"
    assert out["invoice_number"] == "INV-3"
    assert len(out["line_items"]) == 1
    assert out["line_items"][0].get("item") == "GadgetY" or out["line_items"][0].get("name") == "GadgetY"
    assert out["total"] == 50.0


def test_parse_invoice_json(tmp_path):
    path = tmp_path / "inv.json"
    path.write_text('{"vendor": "X", "invoice_number": "INV-1", "date": "2026-01-01", "due_date": "2026-02-01", "line_items": [], "total": 0}')
    out = parse_invoice(path)
    assert out["vendor"] == "X"
    assert out["parser_used"] == "json"
