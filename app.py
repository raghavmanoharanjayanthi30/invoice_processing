"""
Simple Streamlit UI: upload an invoice file, run the pipeline, view results.
Run: streamlit run app.py
"""
import io
import tempfile
from pathlib import Path

import streamlit as st

# Run from project root
ROOT = Path(__file__).resolve().parent


def main():
    st.set_page_config(page_title="Invoice Processing", page_icon="📄")
    st.title("Invoice Processing Pipeline")
    st.caption("Upload an invoice (JSON, CSV, XML, TXT, PDF). We extract data, validate against inventory, approve, and run mock payment.")

    uploaded = st.file_uploader("Choose an invoice file", type=["json", "csv", "xml", "txt", "pdf"])
    if not uploaded:
        st.info("Upload a file to start.")
        return

    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as f:
        f.write(uploaded.getvalue())
        tmp_path = f.name

    try:
        from invoice_graph import run_graph

        path_for_graph = tmp_path
        with st.spinner("Running pipeline…"):
            state = run_graph(path_for_graph)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    status = state.get("overall_status", "unknown")
    if status == "paid":
        st.success(f"**Result: Paid** — Invoice processed and mock payment completed.")
    elif status == "approved":
        st.success("**Result: Approved** — Ready for payment.")
    elif status in ("rejected", "failed"):
        st.error(f"**Result: {status.capitalize()}** — See reasons below.")
    else:
        st.warning(f"**Status:** {status}")

    st.subheader("Ingested data")
    inv = state.get("invoice_data")
    if inv:
        col1, col2 = st.columns(2)
        with col1:
            st.write("**Vendor:**", inv.get("vendor") or "—")
            st.write("**Invoice #:**", inv.get("invoice_number") or "—")
            st.write("**Date / Due:**", inv.get("invoice_date") or "—", " / ", inv.get("due_date") or "—")
        with col2:
            # Show subtotal + tax so total is clear (e.g. 3750 + 375 = 4125)
            subtotal = inv.get("subtotal")
            tax_amount = inv.get("tax_amount")
            total = inv.get("total_amount")
            if subtotal is not None or tax_amount is not None:
                if subtotal is not None:
                    st.write("**Subtotal:**", subtotal)
                if tax_amount is not None:
                    st.write("**Tax:**", tax_amount)
            st.write("**Total:**", total)
            st.write("**Parser:**", state.get("parser_used"))
            if inv.get("extraction_confidence") is not None:
                st.write("**Extraction confidence:**", inv.get("extraction_confidence"))
        line_items = inv.get("line_items") or []
        if line_items:
            st.write("**Line items:**", len(line_items))
            for li in line_items[:15]:
                st.write(f"  - {li.get('item_name') or li.get('item')}: qty {li.get('quantity')}, unit price {li.get('unit_price')}")
    else:
        st.write("No structured data (ingestion may have failed).")

    vr = state.get("validation_result") or {}
    st.subheader("Validation")
    st.write("**Status:**", vr.get("status"))
    for issue in vr.get("issues") or []:
        st.write(f"  - {issue}")
    for item in vr.get("unmatched_items") or []:
        st.write(f"  - Item not in inventory: {item}")

    ar = state.get("approval_result") or {}
    st.subheader("Approval")
    st.write("**Status:**", ar.get("status"))
    for reason in ar.get("reasons") or []:
        st.write(f"  - {reason}")

    pr = state.get("payment_result") or {}
    st.subheader("Payment")
    st.write("**Status:**", pr.get("status"), pr.get("transaction_id") or "")

    if state.get("parse_errors"):
        st.error("Parse errors: " + "; ".join(state["parse_errors"]))


if __name__ == "__main__":
    main()
