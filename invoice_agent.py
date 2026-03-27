"""
invoice_agent.py
----------------
A Claude-powered agent for extracting structured invoice data from PDF files.

Uses tool use with a forced tool call so Claude must return data matching
the defined schema rather than free-form text.

Usage (from Streamlit or any Python script):
    from invoice_agent import run_invoice_agent

    with open("invoice.pdf", "rb") as f:
        records = run_invoice_agent(f.read(), filename="invoice.pdf", api_key="sk-...")

    import pandas as pd
    df = pd.DataFrame(records)
"""

import base64
import os
import anthropic


# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert invoice data extraction agent. Your responsibilities are:
1. Read every page of the provided PDF document thoroughly
2. Identify all invoice records, line items, and sales transactions
3. Extract every available field for each record
4. Call the save_invoice_data tool once with ALL records found

Extraction rules:
- Each line item should be its own record
- Use null for fields not present in the document — do not guess or invent values
- Strip currency symbols from amounts; return numeric values only
- Dates should use ISO format (YYYY-MM-DD) where possible
- State values should be recorded exactly as they appear in the document
- sales_tax on the invoice represents the total tax for the entire invoice, not per line item
- total on the invoice represents the total amount for the entire invoice, not per line item
- sales_tax_rate should be extracted if available (e.g. 0.08 for 8%)
- Do not populate sales_tax or total per line item; those fields reflect invoice-level totals only
"""


# ── Tool definition ────────────────────────────────────────────────────────────

INVOICE_TOOL = {
    "name": "save_invoice_data",
    "description": (
        "Save all structured invoice records extracted from the document. "
        "Call this tool exactly once with the complete list of every record found."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "records": {
                "type": "array",
                "description": "All invoice line items or records found in the document.",
                "items": {
                    "type": "object",
                    "properties": {
                        "invoice_number": {
                            "type": ["string", "null"],
                            "description": "Invoice or document number",
                        },
                        "date": {
                            "type": ["string", "null"],
                            "description": "Invoice date (YYYY-MM-DD preferred)",
                        },
                        "vendor": {
                            "type": ["string", "null"],
                            "description": "Vendor or supplier name",
                        },
                        "customer": {
                            "type": ["string", "null"],
                            "description": "Customer or buyer name",
                        },
                        "description": {
                            "type": ["string", "null"],
                            "description": "Line item description, product, or service",
                        },
                        "quantity": {
                            "type": ["number", "null"],
                            "description": "Quantity of items",
                        },
                        "unit_price": {
                            "type": ["number", "null"],
                            "description": "Price per unit (before tax)",
                        },
                        "amount": {
                            "type": ["number", "null"],
                            "description": "Line item subtotal or invoice amount before tax",
                        },
                        "sales_tax": {
                            "type": ["number", "null"],
                            "description": "Sales tax dollar amount for this record",
                        },
                        "sales_tax_rate": {
                            "type": ["number", "null"],
                            "description": "Sales tax rate as a decimal (e.g. 0.08 for 8%)",
                        },
                        "total": {
                            "type": ["number", "null"],
                            "description": "Total amount including tax",
                        },
                        "state": {
                            "type": ["string", "null"],
                            "description": "US state where the transaction occurred",
                        },
                        "payment_terms": {
                            "type": ["string", "null"],
                            "description": "Payment terms (e.g. Net 30)",
                        },
                    },
                    # additionalProperties: true so Claude can include extra fields
                    # it finds that aren't listed above
                    "additionalProperties": True,
                },
            }
        },
        "required": ["records"],
    },
}


# ── Agent ──────────────────────────────────────────────────────────────────────

def run_invoice_agent(
    pdf_bytes: bytes,
    filename: str = "document.pdf",
    api_key: str | None = None,
) -> list[dict]:
    """
    Extract structured invoice data from a PDF using a Claude tool-use agent.

    Parameters
    ----------
    pdf_bytes : bytes
        Raw PDF content.
    filename : str
        Original filename, added to every returned record as 'Source'.
    api_key : str | None
        Anthropic API key. Falls back to the ANTHROPIC_API_KEY environment
        variable if not provided.

    Returns
    -------
    list[dict]
        One dict per extracted invoice record, ready to pass to pd.DataFrame().

    Raises
    ------
    ValueError
        If the API key is missing or Claude returns no tool call.
    anthropic.APIError
        On any Anthropic API error.
    """
    resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not resolved_key:
        raise ValueError(
            "No API key provided. Pass api_key= or set the ANTHROPIC_API_KEY env var."
        )

    client = anthropic.Anthropic(api_key=resolved_key)
    b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[INVOICE_TOOL],
        # Force Claude to always call this tool — no free-text fallback
        tool_choice={"type": "tool", "name": "save_invoice_data"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Extract all invoice and sales records from this document ({filename}). "
                            "Call save_invoice_data with every record you find."
                        ),
                    },
                ],
            }
        ],
    )

    # Extract the tool input from the response
    for block in response.content:
        if block.type == "tool_use" and block.name == "save_invoice_data":
            records = block.input.get("records", [])
            for record in records:
                record["Source"] = filename
                amount = record.get("amount")
                rate = record.get("sales_tax_rate")
                if amount is not None and rate is not None:
                    tax_per = round(amount * rate, 2)
                    record["sales_tax_per_transaction"] = tax_per
                    record["total_per_transaction"] = round(amount + tax_per, 2)
                else:
                    record["sales_tax_per_transaction"] = None
                    record["total_per_transaction"] = None
            return records

    raise ValueError(
        f"Agent did not return a tool call for {filename}. "
        f"Stop reason: {response.stop_reason}"
    )