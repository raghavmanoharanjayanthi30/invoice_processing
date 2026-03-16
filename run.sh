#!/usr/bin/env bash
# One-command run: checks venv, optional OPENAI_API_KEY, inventory.db, then runs pipeline.
# Usage: ./run.sh data/invoices/invoice_1004.json
#        or: export OPENAI_API_KEY=sk-... && ./run.sh data/invoices/invoice_1008.txt

set -e
cd "$(dirname "$0")"

INVOICE_PATH="${1:-}"

if [ -z "$INVOICE_PATH" ]; then
  echo "Usage: $0 <invoice_path>"
  echo "Example: $0 data/invoices/invoice_1004.json"
  exit 1
fi

# Use venv if present
if [ -d ".venv" ]; then
  . .venv/bin/activate
fi

# Optional: warn if TXT/PDF and no API key
case "${INVOICE_PATH}" in
  *.txt|*.pdf)
    if [ -z "${OPENAI_API_KEY}" ]; then
      echo "Note: OPENAI_API_KEY not set. TXT/PDF extraction will have no structured data (set export OPENAI_API_KEY=sk-... for LLM extraction)."
    fi
    ;;
esac

# Ensure inventory DB exists
if [ ! -f "inventory.db" ]; then
  echo "Creating inventory.db..."
  python setup_inventory.py
fi

python main.py --invoice_path="$INVOICE_PATH"
exit $?
