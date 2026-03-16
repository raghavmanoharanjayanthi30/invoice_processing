#!/usr/bin/env python3
"""
Smoke test: run pipeline on one JSON and one TXT invoice.
Exits 0 only if both complete without pipeline errors (ingestion success; rejection is OK).
"""
import sys
from pathlib import Path

from invoice_graph import run_graph

ROOT = Path(__file__).resolve().parent
INVOICES = ROOT / "data" / "invoices"


def main():
    paths = [
        INVOICES / "invoice_1004.json",
        INVOICES / "invoice_1008.txt",
    ]
    for p in paths:
        if not p.exists():
            print(f"Skip (missing): {p}")
            continue
        rel = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
        try:
            state = run_graph(rel)
            status = state.get("overall_status", "")
            ing = state.get("ingestion_status", "")
            if ing != "success":
                print(f"FAIL {p.name}: ingestion {ing}")
                return 1
            print(f"OK {p.name}: {status}")
        except Exception as e:
            print(f"FAIL {p.name}: {e}")
            return 1
    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
