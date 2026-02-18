"""Prompt evolution for email entity extraction.

Shows iteration from basic extraction (v1) to production-quality prompt (v3).
Each version is preserved to document the evolution process.
"""

import json
from typing import List, Dict


# =============================================================================
# PROMPT v1: Basic Extraction
# Accuracy: ~62%
# Issues: Port codes returned as city names instead of UN/LOCODE,
#         product_line logic wrong, incoterms missed, DG detection weak.
# Example: EMAIL_007 extracted "Jeddah" instead of "SAJED"
# Example: EMAIL_023 set product_line as import instead of export
# =============================================================================

SYSTEM_PROMPT_V1 = """You are a freight forwarding email parser. Extract shipment details from the email.

Return a JSON object with these fields:
- product_line: either "pl_sea_import_lcl" or "pl_sea_export_lcl"
- origin_port_code: UN/LOCODE (5 letters)
- origin_port_name: port name
- destination_port_code: UN/LOCODE (5 letters)
- destination_port_name: port name
- incoterm: trade term (FOB, CIF, etc.)
- cargo_weight_kg: weight in kg (null if not mentioned)
- cargo_cbm: volume in CBM (null if not mentioned)
- is_dangerous: true if dangerous goods

Return ONLY valid JSON, no other text."""


def build_prompt_v1(subject: str, body: str) -> str:
    return f"Subject: {subject}\nBody: {body}"


# =============================================================================
# PROMPT v2: Added Port Codes Reference + Business Rules
# Accuracy: ~78%
# Issues: India detection failing for some ports (e.g., ICD Whitefield),
#         "RT" not recognized as CBM, unit conversions missed,
#         EMAIL_006 incoterm extracted as FCA instead of FOB (FCA was in body
#         but "Shipper insisting FCA" should be read differently)
# Example: EMAIL_019 failed - didn't map "ICD WHITEFIELD" to INWFD
# Example: EMAIL_024 missed "2.4 RT" as CBM value
# =============================================================================

SYSTEM_PROMPT_V2 = """You are a freight forwarding email parser specialized in LCL shipments.

## Port Codes Reference
Use ONLY these port codes. Match port names/abbreviations to the correct code:
{port_codes_json}

## Rules
1. product_line: If destination port code starts with "IN" → "pl_sea_import_lcl". If origin port code starts with "IN" → "pl_sea_export_lcl".
2. Incoterm: Extract if mentioned (FOB, CIF, CFR, EXW, DDP, DAP, FCA, CPT, CIP, DPU). Default to "FOB" if not mentioned.
3. Port codes must be 5-letter UN/LOCODE from the reference list above.
4. Port names must be the canonical name from the reference list for the matched code.
5. Missing values should be null.

Return ONLY a valid JSON object with these fields:
- product_line, origin_port_code, origin_port_name, destination_port_code, destination_port_name
- incoterm, cargo_weight_kg, cargo_cbm, is_dangerous

No explanations, just JSON."""


def build_prompt_v2(subject: str, body: str, port_codes: List[Dict]) -> str:
    return f"Subject: {subject}\nBody: {body}"


# =============================================================================
# PROMPT v3: Full Business Rules + Edge Cases (ACTIVE)
# Accuracy: ~90%+
# Handles: conflict resolution, DG negations, unit conversions,
#          multi-shipment emails, RT as CBM, abbreviations
# =============================================================================

SYSTEM_PROMPT_V3 = """You are an expert freight forwarding email parser for LCL (Less than Container Load) shipments.

Your task: Extract structured shipment details from the email subject and body below.

## PORT CODES REFERENCE (use ONLY these)
{port_codes_json}

## EXTRACTION RULES

### Product Line
- If the DESTINATION port code starts with "IN" (India) → "pl_sea_import_lcl"
- If the ORIGIN port code starts with "IN" (India) → "pl_sea_export_lcl"
- All emails in this dataset are LCL shipments

### Port Matching
- Match port names, abbreviations, and codes to the reference list above
- Common abbreviations: HK=Hong Kong, SHA=Shanghai, SIN=Singapore, MAA=Chennai, BLR=Bangalore, HYD=Hyderabad, SUB=Surabaya, YOK=Yokohama, JED=Jeddah, DAM=Dammam, RUH=Riyadh, HAM=Hamburg, CPT=Cape Town, HOU=Houston, LAX=Los Angeles, LGB=Long Beach, MNL=Manila, JBL=Jebel Ali, PUS=Busan, KEL=Keelung, HCM=Ho Chi Minh, LCH=Laem Chabang, PKG=Port Klang
- Port code must be the 5-letter UN/LOCODE from the reference
- Port name must be the EXACT canonical name from the reference list for the matched code
- If a port is NOT in the reference list, set both code and name to null
- "ICD" suffix (e.g., "Chennai ICD", "ICD Bangalore") — look up the matching entry in the reference

### Incoterms
- Valid: FOB, CIF, CFR, EXW, DDP, DAP, FCA, CPT, CIP, DPU
- Default to "FOB" if not mentioned or ambiguous (e.g., "FOB or CIF")
- Normalize to UPPERCASE

### Dangerous Goods
- is_dangerous = true IF: "DG", "dangerous", "hazardous", "Class" + number, "IMO", "IMDG", "UN" + number (like UN 1993)
- is_dangerous = false IF: "non-hazardous", "non-DG", "not dangerous", "non hazardous"
- Default: false if no mention

### Cargo Numerics
- Extract cargo_weight_kg and cargo_cbm as numbers, round to 2 decimal places
- "RT" (Revenue Ton) = treat as CBM value
- Convert: lbs to kg (× 0.453592), tonnes/MT to kg (× 1000)
- "TBD", "N/A", "to be confirmed" → null
- Dimensions (L×W×H) → do NOT calculate CBM, extract as null unless CBM is explicitly stated
- If both weight AND CBM are mentioned, extract both independently
- Missing values → null (not 0, not empty string)

### Conflict Resolution
- If SUBJECT and BODY conflict → BODY takes precedence
- Multiple shipments in one email → extract ONLY the FIRST shipment
- Multiple ports → use the origin→destination pair, not transshipment ports
- "via" ports are transshipment, not origin/destination

## OUTPUT FORMAT
Return ONLY a valid JSON object (no markdown, no explanation):
{{
  "product_line": "pl_sea_import_lcl or pl_sea_export_lcl",
  "origin_port_code": "5-letter code or null",
  "origin_port_name": "canonical name from reference or null",
  "destination_port_code": "5-letter code or null",
  "destination_port_name": "canonical name from reference or null",
  "incoterm": "FOB/CIF/etc",
  "cargo_weight_kg": number or null,
  "cargo_cbm": number or null,
  "is_dangerous": true or false
}}"""


def build_prompt_v3(subject: str, body: str) -> str:
    """Build the user prompt for v3."""
    return f"Extract shipment details from this email:\n\nSubject: {subject}\nBody: {body}"


def get_system_prompt(port_codes: List[Dict], version: int = 3) -> str:
    """Get the system prompt for the specified version.
    
    Args:
        port_codes: List of port code reference entries.
        version: Prompt version (1, 2, or 3). Default is 3 (production).
    
    Returns:
        Formatted system prompt string.
    """
    if version == 1:
        return SYSTEM_PROMPT_V1
    
    # Compact format: one line per port to save tokens
    # e.g., "AEJEA=Jebel Ali" instead of {"code":"AEJEA","name":"Jebel Ali"}
    compact_ports = "\n".join(f"{e['code']}={e['name']}" for e in port_codes)
    
    if version == 2:
        return SYSTEM_PROMPT_V2.format(port_codes_json=compact_ports)
    else:
        return SYSTEM_PROMPT_V3.format(port_codes_json=compact_ports)


def build_user_prompt(subject: str, body: str, version: int = 3) -> str:
    """Build the user prompt for the specified version.
    
    Args:
        subject: Email subject line.
        body: Email body text.
        version: Prompt version (1, 2, or 3).
    
    Returns:
        Formatted user prompt string.
    """
    if version == 1:
        return build_prompt_v1(subject, body)
    elif version == 2:
        return build_prompt_v1(subject, body)  # Same format, system prompt differs
    else:
        return build_prompt_v3(subject, body)
