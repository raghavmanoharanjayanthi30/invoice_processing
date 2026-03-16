# Invoice Processing Automation

## What was developed in this project

An **invoice processing pipeline** that ingests documents (JSON, CSV, XML, TXT, PDF), extracts structured data (with LLM for TXT/PDF), validates against inventory, applies approval rules, and runs a mock payment. **Business impact:** reduces manual data entry, catches out-of-stock and unknown items before payment, and provides a full audit trail with reasons for every decision.

## Background

Invoices arrive via email as PDFs in messy formats with frequent errors. Staff manually extract data, validate against a legacy inventory database (inconsistent), obtain VP approval (via email chains), and process payment (via a banking API).

**Current pain points:**
- High error rate
- Long processing delays
- Frustrated stakeholders

## Objective

Build a **multi-agent system** that automates the end-to-end invoice processing workflow. The system must run as a working prototype — not just designs or slides.

## Workflow

The system handles four stages:

1. **Ingestion** — Extract structured data from invoice documents (PDFs, text files). Fields include: Vendor, Amount, Items (with quantities), and Due Date. Expect unstructured text, typos, missing data, and potentially fraudulent entries.

2. **Validation** — Verify extracted data against a mock inventory database (SQLite). Flag mismatches such as quantity exceeding available stock or items not found in inventory.

3. **Approval** — Simulate VP-level review with rule-based decision-making (e.g., invoices over $10K require additional scrutiny). The agent should reason through approval/rejection with a reflection or critique loop.

4. **Payment** — If approved, call a mock payment function. If rejected, log the rejection with reasoning.

Sample invoices are provided in the `data/invoices/` directory in various formats (PDF, CSV, JSON, TXT). Use these as inputs for testing. The data intentionally includes a mix of clean entries and problematic ones — identifying and handling issues is part of the challenge.

### Mock Inventory Database (Required Setup)

Before running the system, you **must** create a local SQLite database that the validation agent will check invoices against. The sample invoices in `data/invoices/` reference specific items and quantities — your database needs to contain matching inventory records so the validation stage can flag mismatches, out-of-stock items, and unknown products.

Below is a starter schema and seed data that covers the core items referenced across the provided invoices:

```python
import sqlite3

conn = sqlite3.connect('inventory.db')  # Persist to file so all agents can access it
cursor = conn.cursor()

cursor.execute('CREATE TABLE IF NOT EXISTS inventory (item TEXT PRIMARY KEY, stock INTEGER)')
cursor.execute("""
    INSERT INTO inventory VALUES
    ('WidgetA', 15),
    ('WidgetB', 10),
    ('GadgetX', 5),
    ('FakeItem', 0)
""")
conn.commit()
```

**Why this matters:** The sample invoices are designed to test your validation logic against this database. For example:

| Scenario | Invoice | What should happen |
|---|---|---|
| Normal order within stock | INV-1001, INV-1004, INV-1006 | Items found, quantities valid — passes validation |
| Quantity exceeds stock | INV-1002 (requests 20× GadgetX, only 5 in stock) | Flagged as stock mismatch |
| Fraudulent / zero-stock item | INV-1003 (references FakeItem, 0 stock) | Flagged as out of stock or suspicious |
| Item not in database at all | INV-1008 (SuperGizmo, MegaSprocket), INV-1016 (WidgetC) | Flagged as unknown item |
| Invalid data | INV-1009 (negative quantity) | Flagged as data integrity issue |

You may extend the seed data with additional items or columns (e.g., unit price, category) to support richer validation — the above is the minimum needed to exercise the provided test invoices. If you want your system to also validate pricing or vendor information, consider adding tables for those as well.

### Mock Payment API

```python
def mock_payment(vendor, amount):
    print(f"Paid {amount} to {vendor}")
    return {"status": "success"}
```

### Grok API Setup

```python
from xai import Grok

client = Grok(api_key="your_key")
response = client.chat.completions.create(
    model="grok-3",
    messages=[{"role": "user", "content": "Reason about this..."}]
)
```

## Setup & Running the System

### 1. Python environment

```bash
cd invoice_processing
python3.12 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. OpenAI API key (for TXT/PDF extraction)

TXT and PDF invoices use OpenAI to extract structured data. **Export your key** (get one at https://platform.openai.com/api-keys), then run:

```bash
export OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
python setup_inventory.py
python main.py --invoice_path=data/invoices/invoice_1008.txt
```

No `.env` file needed. (Optional: copy `.env.example` to `.env` and add your key if you prefer file-based config.)

### 3. Inventory database (for validation)

Create the SQLite inventory DB once:

```bash
python setup_inventory.py
```

### 4. Run invoice processing

```bash
python main.py --invoice_path=data/invoices/invoice_1004.json
python main.py --invoice_path=data/invoices/invoice_1008.txt
python main.py --invoice_path=data/invoices/invoice_1011.pdf
```

Add `--json` to print the full final state as JSON. Use `--quiet` for a single-line result only.

**Exit codes (for scripting):** `0` = pipeline ran successfully (invoice paid or approved); `1` = rejected, failed, or error (e.g. missing path, ingestion failed, validation/approval rejected).

### One-command run

```bash
./run.sh data/invoices/invoice_1004.json
```

The script checks for a venv, creates `inventory.db` if missing, and runs the pipeline. For TXT/PDF, set `export OPENAI_API_KEY=sk-...` first if you want LLM extraction.

### Smoke test

```bash
python smoke_test.py
```

Runs one JSON and one TXT invoice; exits 0 only if both complete without pipeline errors.

### Agents

The pipeline is implemented as four agents in a LangGraph:

| Agent | Role | Inputs | Outputs |
|-------|------|--------|---------|
| **Extractor** | Parse file and extract structured invoice data (LLM for TXT/PDF). | `invoice_path` | `invoice_data`, `raw_parser_output`, `ingestion_status` |
| **Validator** | Check line items and quantities against inventory DB; flag missing/invalid data. | `invoice_data` | `validation_result` (issues, unmatched_items, stock_mismatches) |
| **Approver** | Rule-based approve/reject/manual_review (e.g. amount &gt; $10K, suspected fraud). | `invoice_data`, `validation_result` | `approval_result` |
| **Payer** | Call mock payment if approved; otherwise skip. | `invoice_data`, `approval_result` | `payment_result` |

### Architecture

The pipeline is implemented as a **LangGraph** state machine with a single **`InvoiceProcessingState`** (typed dict) that flows through all nodes. State holds: `invoice_path`, `invoice_data`, `raw_parser_output`, `validation_result`, `approval_result`, `payment_result`, `overall_status`, and `processing_logs`. Each agent returns a partial update that is merged into the state.

**Graph flow:** `START → Ingest → Validate → [Re-extract?] → Approve → [Pay | END] → END`. After validation, if the result is **failed** and the invoice was LLM-extracted (TXT/PDF with `+llm`), the graph can take an optional **re-extract** step (see below) and then re-run validation once before proceeding to approval.

**Parsers:** Format-specific parsers (JSON, CSV row-based, CSV key-value, XML, TXT, PDF) live in `invoice_parsers.py`. CSV and PDF use **fallbacks** (e.g. try row-based CSV, then key-value; try pdfplumber, then PyMuPDF). TXT and PDF use **OpenAI** for structured extraction when `OPENAI_API_KEY` is set; otherwise they return raw text only.

### Additional features

- **Extraction retry:** If validation fails on an LLM-extracted invoice (TXT/PDF), the graph runs a **single retry**: the LLM is called again with the validation issues (e.g. "Item X not found in inventory", "Requested quantity exceeds stock") and asked to re-extract. Updated `invoice_data` is re-validated before moving to approval. This implements a lightweight self-correction loop.
- **LLM extraction (TXT/PDF):** OpenAI is used to extract vendor, invoice number, dates, line items, total, subtotal, tax, and optional **extraction_confidence** (0–1) from free-form text. Prompt and JSON-mode output are in `llm_extract.py`.
- **Suggested next steps:** When an invoice is rejected or failed, the CLI (and schema) can output 1–3 **suggested next steps** (e.g. "Add SuperGizmo, MegaSprocket to inventory or contact vendor", "Fix missing fields or set OPENAI_API_KEY for TXT/PDF").
- **Observability:** All agent steps append to **`processing_logs`**; the pipeline uses Python **logging** (`invoice_graph` logger) so logs can be routed to files or handlers. CLI prints full reasons (validation issues, approval reasons, payment failures).
- **CLI options:** `--quiet` (one-line result), `--no-color`, `--json` (full state). Exit code `0` for success, `1` for rejected/failed.
- **Streamlit UI:** Upload a file and see ingested data (including subtotal/tax/total), validation, approval, and payment in the browser.
- **One-command run:** `run.sh` checks venv, warns if TXT/PDF and no API key, creates `inventory.db` if missing, then runs the pipeline.
- **Smoke test:** `smoke_test.py` runs one JSON and one TXT invoice and exits 0 only if both complete without errors.
- **Unit tests:** `pytest tests/` covers schema (`initial_state`, `raw_parser_output_to_invoice_data`), parsers (JSON, CSV, XML, `parse_invoice`), and graph (`build_graph`, `run_graph` with a project invoice).

### Web UI (Streamlit)

Run the Streamlit app to upload invoices and see results in the browser.

**Setup (one-time):**

1. Open a terminal and go to the project folder:
   ```bash
   cd invoice_processing
   ```

2. Create and activate a virtual environment:
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```
   On Windows: `.venv\Scripts\activate`

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. (Optional) For TXT/PDF invoices, set your OpenAI API key so the app can extract structured data:
   ```bash
   export OPENAI_API_KEY=sk-your-key-here
   ```

5. Create the inventory database (needed for validation):
   ```bash
   python setup_inventory.py
   ```

**Run the app:**

```bash
streamlit run app.py
```

Your browser will open to `http://localhost:8501`. Use the file uploader to choose an invoice (JSON, CSV, XML, TXT, or PDF). The app will show ingested data (vendor, dates, **subtotal**, **tax**, **total**, line items), validation result, approval, and payment status. To stop the app, press `Ctrl+C` in the terminal.

### Tests

```bash
pytest tests/ -v
```

### Limitations

- **TXT/PDF** require `OPENAI_API_KEY` for structured extraction; without it, only raw text is stored and validation will fail on missing fields.
- **Approval** is rule-based only (no LLM reasoning).
- No batch mode (one invoice per run).
- Payment is a mock; no real API.

## Evaluation Criteria

- **Functionality** — Does the system work end-to-end?
- **Code Quality** — Clean, testable, well-structured code with error handling and observability
- **Agentic Sophistication** — LLM integration, multi-agent flow, tool use, self-correction loops
- **Shipping Mindset** — Valuable MVP delivered under ambiguity; scope ruthlessly cut where needed
- **Presentation** — Clear translation of technical decisions to business impact
- **Above/Beyond** - Have you made it your own? Implemented additional features that make the solution feel great? Expanded assumptions? Added to test cases?
- **UI/UX** - Users will understand and enjoy using this system.
