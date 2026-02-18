"""Main extraction script — processes emails via Groq LLM and outputs structured data.

Usage:
    python extract.py          # Process all 50 emails, generate output.json
    python extract.py --dry    # Dry run: print prompt for first email only
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Fix Windows console encoding for Unicode characters
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from groq import Groq
from pydantic import ValidationError

from schemas import EmailInput, PortCode, ShipmentExtraction, create_null_extraction
from prompts import get_system_prompt, build_user_prompt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
MODEL = "llama-3.3-70b-versatile"  # llama-3.1 decommissioned; README fallback
TEMPERATURE = 0
MAX_RETRIES = 5
BASE_DELAY = 10  # seconds, for exponential backoff

DATA_DIR = Path(__file__).resolve().parent.parent  # parent dir with JSON files
OUTPUT_FILE = Path(__file__).resolve().parent / "output.json"

PROMPT_VERSION = 3  # Active prompt version


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(filename: str) -> List[Dict]:
    """Load a JSON file from the data directory."""
    filepath = DATA_DIR / filename
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def load_port_codes() -> List[Dict]:
    """Load port codes reference."""
    return load_json("port_codes_reference.json")


def load_emails() -> List[EmailInput]:
    """Load and validate input emails."""
    raw = load_json("emails_input.json")
    return [EmailInput(**email) for email in raw]


def build_port_lookup(port_codes: List[Dict]) -> Dict[str, str]:
    """Build a code→canonical_name lookup from port codes.
    
    For codes with multiple name entries, we use the first (shortest) name
    as the primary canonical name, but store all variants for matching.
    """
    code_to_names: Dict[str, List[str]] = {}
    for entry in port_codes:
        code = entry["code"]
        name = entry["name"]
        if code not in code_to_names:
            code_to_names[code] = []
        code_to_names[code].append(name)
    return code_to_names


def find_canonical_name(port_codes: List[Dict], code: Optional[str], name_hint: Optional[str] = None) -> Optional[str]:
    """Find the canonical port name for a given code from the reference list.
    
    If name_hint is provided and matches one of the reference names for that code,
    use it. Otherwise, use the first name entry for the code.
    """
    if code is None:
        return None
    
    matching_entries = [e for e in port_codes if e["code"] == code]
    if not matching_entries:
        return None
    
    # If the LLM returned a name that exactly matches a reference entry, use it
    if name_hint:
        name_hint_clean = name_hint.strip()
        for entry in matching_entries:
            if entry["name"].lower() == name_hint_clean.lower():
                return entry["name"]
    
    # Default to the first entry's name
    return matching_entries[0]["name"]


def find_port_code_by_name(port_codes: List[Dict], name: Optional[str]) -> Optional[str]:
    """Try to find a port code by matching against reference names."""
    if not name:
        return None
    
    name_lower = name.strip().lower()
    
    # Exact match
    for entry in port_codes:
        if entry["name"].lower() == name_lower:
            return entry["code"]
    
    # Partial match (name contained in reference name or vice versa)
    for entry in port_codes:
        ref_lower = entry["name"].lower()
        if name_lower in ref_lower or ref_lower in name_lower:
            return entry["code"]
    
    return None


def parse_llm_response(raw_text: str) -> Dict[str, Any]:
    """Parse JSON from LLM response, handling markdown code blocks."""
    text = raw_text.strip()
    
    # Remove markdown code block if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    
    # Find JSON object in the response
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    
    return json.loads(text)


def post_process(raw: Dict[str, Any], email_id: str, port_codes: List[Dict]) -> ShipmentExtraction:
    """Post-process LLM output: validate port codes, apply canonical names, build model."""
    
    # Ensure id is set
    raw["id"] = email_id
    
    # Validate and fix port codes against reference
    origin_code = raw.get("origin_port_code")
    dest_code = raw.get("destination_port_code")
    origin_name = raw.get("origin_port_name")
    dest_name = raw.get("destination_port_name")
    
    # If code not in reference, try to look up by name
    ref_codes = {e["code"] for e in port_codes}
    
    if origin_code and origin_code not in ref_codes:
        looked_up = find_port_code_by_name(port_codes, origin_name)
        if looked_up:
            origin_code = looked_up
        else:
            origin_code = None
    
    if dest_code and dest_code not in ref_codes:
        looked_up = find_port_code_by_name(port_codes, dest_name)
        if looked_up:
            dest_code = looked_up
        else:
            dest_code = None
    
    # Apply canonical names from reference
    raw["origin_port_code"] = origin_code
    raw["origin_port_name"] = find_canonical_name(port_codes, origin_code, origin_name)
    raw["destination_port_code"] = dest_code
    raw["destination_port_name"] = find_canonical_name(port_codes, dest_code, dest_name)
    
    # If code is null, name must also be null
    if raw["origin_port_code"] is None:
        raw["origin_port_name"] = None
    if raw["destination_port_code"] is None:
        raw["destination_port_name"] = None
    
    # Determine product_line based on port codes
    if dest_code and dest_code.startswith("IN"):
        raw["product_line"] = "pl_sea_import_lcl"
    elif origin_code and origin_code.startswith("IN"):
        raw["product_line"] = "pl_sea_export_lcl"
    # else keep what LLM returned
    
    # Build Pydantic model (validates and rounds numerics)
    try:
        return ShipmentExtraction(**raw)
    except ValidationError as e:
        print(f"  [WARN] Validation error for {email_id}: {e}")
        return create_null_extraction(email_id)


# ---------------------------------------------------------------------------
# LLM Call
# ---------------------------------------------------------------------------

def extract_single(
    client: Groq,
    email: EmailInput,
    system_prompt: str,
    port_codes: List[Dict],
) -> ShipmentExtraction:
    """Extract shipment details from a single email using the Groq LLM."""
    
    user_prompt = build_user_prompt(email.subject, email.body, version=PROMPT_VERSION)
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=TEMPERATURE,
                max_tokens=1024,
            )
            
            raw_text = response.choices[0].message.content
            parsed = parse_llm_response(raw_text)
            return post_process(parsed, email.id, port_codes)
        
        except json.JSONDecodeError as e:
            print(f"  [WARN] JSON parse error for {email.id} (attempt {attempt}): {e}")
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** (attempt - 1))
                time.sleep(delay)
            else:
                return create_null_extraction(email.id)
        
        except Exception as e:
            error_msg = str(e).lower()
            if "rate_limit" in error_msg or "429" in error_msg or "timeout" in error_msg:
                delay = BASE_DELAY * (2 ** (attempt - 1))
                print(f"  [WAIT] Rate limit/timeout for {email.id} (attempt {attempt}), retrying in {delay}s...")
                time.sleep(delay)
            else:
                print(f"  [ERROR] Error for {email.id} (attempt {attempt}): {e}")
                if attempt < MAX_RETRIES:
                    delay = BASE_DELAY * (2 ** (attempt - 1))
                    time.sleep(delay)
                else:
                    return create_null_extraction(email.id)
    
    return create_null_extraction(email.id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Process all emails and generate output.json."""
    
    # Validate API key
    if not GROQ_API_KEY or GROQ_API_KEY == "your-api-key-here":
        print("[ERROR] GROQ_API_KEY not set. Copy .env.example to .env and add your key.")
        sys.exit(1)
    
    # Load data
    print("[INFO] Loading data files...")
    port_codes = load_port_codes()
    emails = load_emails()
    print(f"   Loaded {len(port_codes)} port codes, {len(emails)} emails")
    
    # Initialize Groq client
    client = Groq(api_key=GROQ_API_KEY)
    system_prompt = get_system_prompt(port_codes, version=PROMPT_VERSION)
    
    # Check for dry run
    if "--dry" in sys.argv:
        print("\n[DRY RUN] Prompt for first email:\n")
        print("=== SYSTEM PROMPT ===")
        print(system_prompt[:500] + "...\n")
        print("=== USER PROMPT ===")
        print(build_user_prompt(emails[0].subject, emails[0].body, version=PROMPT_VERSION))
        return
    
    # Process all emails
    results: List[Dict] = []
    total = len(emails)
    start_time = time.time()
    
    print(f"\n[START] Processing {total} emails with prompt v{PROMPT_VERSION}...\n")
    
    for i, email in enumerate(emails, 1):
        print(f"[{i:2d}/{total}] {email.id}: {email.subject[:50]}...")
        
        extraction = extract_single(client, email, system_prompt, port_codes)
        results.append(extraction.model_dump())
        
        # Delay between requests to avoid rate limiting (Groq free: ~30 req/min)
        if i < total:
            time.sleep(3)
    
    elapsed = time.time() - start_time
    
    # Save output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n[DONE] Processed {total} emails in {elapsed:.1f}s")
    print(f"[INFO] Results saved to {OUTPUT_FILE}")
    
    # Quick summary
    null_count = sum(1 for r in results if r["origin_port_code"] is None)
    dg_count = sum(1 for r in results if r["is_dangerous"])
    export_count = sum(1 for r in results if r["product_line"] == "pl_sea_export_lcl")
    print(f"   Imports: {total - export_count}, Exports: {export_count}")
    print(f"   Dangerous: {dg_count}, Null ports: {null_count}")


if __name__ == "__main__":
    main()
