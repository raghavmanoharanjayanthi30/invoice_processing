"""
Invoice parsers by file type. All parsers return a normalized dict:
  vendor, invoice_number, date, due_date, line_items, total, subtotal, tax_amount, raw_text?, parser_used
"""
from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd


# --- Normalized output keys ---
def _normalize_line_item(item: str | None = None, name: str | None = None, quantity=None, qty=None, unit_price=None):
    """Build a single line-item dict with consistent keys."""
    q = quantity if quantity is not None else qty
    if q is not None and not isinstance(q, int):
        try:
            q = int(float(q))
        except (TypeError, ValueError):
            q = None
    up = unit_price
    if up is not None and not isinstance(up, (int, float)):
        try:
            up = float(up)
        except (TypeError, ValueError):
            up = None
    return {
        "item": item or name or "",
        "quantity": q,
        "unit_price": up,
    }


# --- JSON ---
def parse_json(path: Path | str) -> dict:
    path = Path(path)
    with open(path, "r") as f:
        data = json.load(f)
    vendor = data.get("vendor")
    if isinstance(vendor, dict):
        vendor = vendor.get("name") or str(vendor)
    line_items = []
    for li in data.get("line_items") or []:
        line_items.append(_normalize_line_item(
            item=li.get("item") or li.get("name"),
            quantity=li.get("quantity") or li.get("qty"),
            unit_price=li.get("unit_price"),
        ))
    return {
        "vendor": vendor,
        "invoice_number": data.get("invoice_number"),
        "date": data.get("date"),
        "due_date": data.get("due_date"),
        "line_items": line_items,
        "total": data.get("total"),
        "subtotal": data.get("subtotal"),
        "tax_amount": data.get("tax_amount"),
        "parser_used": "json",
    }


def _raw_text_to_parser_output(content: str, parser_used: str) -> dict:
    """
    Convert raw text to parser output using LLM extraction (if OPENAI_API_KEY set).
    When key is missing or LLM fails, returns minimal result with raw_text only.
    """
    try:
        from llm_extract import extract_invoice_from_text
        llm_result = extract_invoice_from_text(content)
        if llm_result is not None:
            llm_result["parser_used"] = parser_used + "+llm"
            return llm_result
    except Exception:
        pass
    return {
        "vendor": None,
        "invoice_number": None,
        "date": None,
        "due_date": None,
        "line_items": [],
        "total": None,
        "subtotal": None,
        "tax_amount": None,
        "raw_text": content,
        "parser_used": parser_used,
    }


# --- TXT (free-form / email-style): raw text + LLM or heuristic extraction ---
def parse_txt(path: Path | str) -> dict:
    path = Path(path)
    with open(path, "r") as f:
        content = f.read()
    return _raw_text_to_parser_output(content, "txt")


# --- CSV: row-per-line-item style (e.g. invoice_1015, invoice_1007) ---
def parse_csv_row_based(path: Path | str) -> dict:
    path = Path(path)
    df = pd.read_csv(path)
    required = ["Item", "Qty", "Unit Price"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Row-based CSV missing column: {col}")
    line_rows = df[
        df["Item"].notna()
        & (df["Item"].astype(str).str.strip() != "")
        & (~df["Item"].astype(str).str.startswith("Subtotal"))
        & (~df["Item"].astype(str).str.startswith("Tax"))
        & (~df["Item"].astype(str).str.startswith("Total"))
    ].dropna(subset=["Item"])
    if line_rows.empty:
        raise ValueError("No line-item rows found in row-based CSV")
    line_items = []
    for _, row in line_rows.iterrows():
        line_items.append(_normalize_line_item(
            item=str(row["Item"]).strip(),
            quantity=row.get("Qty"),
            unit_price=row.get("Unit Price"),
        ))
    header_cols = ["Invoice Number", "Vendor", "Date", "Due Date"]
    first = line_rows.iloc[0]
    total = None
    if "Line Total" in df.columns:
        total = line_rows["Line Total"].sum()
    return {
        "vendor": first.get("Vendor") if "Vendor" in first else None,
        "invoice_number": first.get("Invoice Number") if "Invoice Number" in first else None,
        "date": first.get("Date") if "Date" in first else None,
        "due_date": first.get("Due Date") if "Due Date" in first else None,
        "line_items": line_items,
        "total": total,
        "subtotal": None,
        "tax_amount": None,
        "parser_used": "csv_row_based",
    }


# --- CSV: key-value style (e.g. invoice_1006) ---
def parse_csv_key_value(path: Path | str) -> dict:
    path = Path(path)
    kv = pd.read_csv(path)
    if "field" not in kv.columns or "value" not in kv.columns:
        raise ValueError("Key-value CSV must have 'field' and 'value' columns")
    item_names = kv[kv["field"] == "item"]["value"].astype(str).tolist()
    qty = kv[kv["field"] == "quantity"]["value"].tolist()
    price = kv[kv["field"] == "unit_price"]["value"].tolist()
    if len(item_names) != len(qty) or len(qty) != len(price):
        raise ValueError("Key-value CSV: mismatched item/quantity/unit_price rows")
    line_items = [
        _normalize_line_item(item=item_names[i], qty=qty[i], unit_price=price[i])
        for i in range(len(item_names))
    ]
    header = kv[~kv["field"].isin(("item", "quantity", "unit_price"))].set_index("field")["value"].to_dict()
    total = header.get("total")
    if total is not None:
        try:
            total = float(total)
        except (TypeError, ValueError):
            pass
    return {
        "vendor": header.get("vendor"),
        "invoice_number": header.get("invoice_number"),
        "date": header.get("date"),
        "due_date": header.get("due_date"),
        "line_items": line_items,
        "total": total,
        "subtotal": header.get("subtotal"),
        "tax_amount": header.get("tax"),
        "parser_used": "csv_key_value",
    }


# --- XML ---
def _xml_get_text(el, tag: str, default: str = ""):
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else default


def parse_xml(path: Path | str) -> dict:
    path = Path(path)
    tree = ET.parse(path)
    root = tree.getroot()
    header = root.find("header")
    header_dict = {}
    if header is not None:
        for e in header:
            header_dict[e.tag] = (e.text or "").strip()
    line_items = []
    for item in root.findall(".//line_items/item"):
        line_items.append(_normalize_line_item(
            name=_xml_get_text(item, "name"),
            quantity=_xml_get_text(item, "quantity", "0"),
            unit_price=_xml_get_text(item, "unit_price", "0"),
        ))
    totals = root.find("totals")
    total = None
    if totals is not None:
        t = _xml_get_text(totals, "total")
        if t:
            try:
                total = float(t)
            except ValueError:
                total = t
    return {
        "vendor": header_dict.get("vendor"),
        "invoice_number": header_dict.get("invoice_number"),
        "date": header_dict.get("date"),
        "due_date": header_dict.get("due_date"),
        "line_items": line_items,
        "total": total,
        "subtotal": float(_xml_get_text(totals, "subtotal")) if totals is not None and _xml_get_text(totals, "subtotal") else None,
        "tax_amount": float(_xml_get_text(totals, "tax_amount")) if totals is not None and _xml_get_text(totals, "tax_amount") else None,
        "parser_used": "xml",
    }


# --- PDF (pdfplumber first, PyMuPDF fallback); uses LLM when API key set ---
def parse_pdf_pdfplumber(path: Path | str) -> dict:
    import pdfplumber
    path = Path(path)
    with pdfplumber.open(path) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    return _raw_text_to_parser_output(text, "pdf_pdfplumber")


def parse_pdf_pymupdf(path: Path | str) -> dict:
    import fitz
    path = Path(path)
    doc = fitz.open(path)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    return _raw_text_to_parser_output(text, "pdf_pymupdf")


def parse_pdf(path: Path | str) -> dict:
    """Try pdfplumber first, then PyMuPDF."""
    errors = []
    try:
        return parse_pdf_pdfplumber(path)
    except Exception as e:
        errors.append(("pdfplumber", e))
    try:
        return parse_pdf_pymupdf(path)
    except Exception as e:
        errors.append(("pymupdf", e))
    raise RuntimeError(f"All PDF parsers failed: {errors}") from errors[-1][1]


# --- Main dispatcher with fallbacks ---
def parse_invoice(path: Path | str) -> dict:
    """
    Parse an invoice file. Chooses parser by extension; uses fallbacks for CSV and PDF.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")
    suffix = path.suffix.lower()

    if suffix == ".json":
        return parse_json(path)

    if suffix == ".txt":
        return parse_txt(path)

    if suffix == ".xml":
        return parse_xml(path)

    if suffix == ".csv":
        errors = []
        for parser_fn, name in [(parse_csv_row_based, "csv_row_based"), (parse_csv_key_value, "csv_key_value")]:
            try:
                return parser_fn(path)
            except Exception as e:
                errors.append((name, e))
        raise RuntimeError(f"All CSV parsers failed: {errors}") from errors[-1][1]

    if suffix == ".pdf":
        return parse_pdf(path)

    raise ValueError(f"Unsupported invoice format: {suffix}. Use .json, .txt, .csv, .xml, or .pdf")


def parse_invoices(paths: list[Path | str]) -> list[dict]:
    """Parse multiple invoice paths. Returns list of results (one dict per path)."""
    return [parse_invoice(p) for p in paths]
