"""Pydantic models for email entity extraction."""

from typing import Literal, Optional
from pydantic import BaseModel, field_validator


class EmailInput(BaseModel):
    """Input email schema."""
    id: str
    subject: str
    body: str
    sender_email: str = ""
    to_emails: str = ""
    cc_emails: str = ""


class PortCode(BaseModel):
    """Port code reference entry."""
    code: str
    name: str


class ShipmentExtraction(BaseModel):
    """Extracted shipment details from an email.
    
    All 9 evaluated fields + id identifier.
    """
    id: str
    product_line: Literal["pl_sea_import_lcl", "pl_sea_export_lcl"]
    origin_port_code: Optional[str] = None
    origin_port_name: Optional[str] = None
    destination_port_code: Optional[str] = None
    destination_port_name: Optional[str] = None
    incoterm: str = "FOB"
    cargo_weight_kg: Optional[float] = None
    cargo_cbm: Optional[float] = None
    is_dangerous: bool = False

    @field_validator("cargo_weight_kg", "cargo_cbm", mode="before")
    @classmethod
    def round_numeric(cls, v: Optional[float]) -> Optional[float]:
        """Round numeric fields to 2 decimal places. Reject negatives."""
        if v is None:
            return None
        v = float(v)
        if v < 0:
            return None
        return round(v, 2)

    @field_validator("incoterm", mode="before")
    @classmethod
    def normalize_incoterm(cls, v: str) -> str:
        """Normalize incoterm to uppercase. Default to FOB if invalid."""
        valid_incoterms = {"FOB", "CIF", "CFR", "EXW", "DDP", "DAP", "FCA", "CPT", "CIP", "DPU"}
        if v is None:
            return "FOB"
        v = str(v).strip().upper()
        if v not in valid_incoterms:
            return "FOB"
        return v

    @field_validator("origin_port_name", "destination_port_name", mode="before")
    @classmethod
    def nullify_empty_strings(cls, v: Optional[str]) -> Optional[str]:
        """Convert empty strings to None."""
        if v is None or str(v).strip() == "":
            return None
        return str(v).strip()

    @field_validator("origin_port_code", "destination_port_code", mode="before")
    @classmethod
    def nullify_empty_port_codes(cls, v: Optional[str]) -> Optional[str]:
        """Convert empty strings to None, uppercase port codes."""
        if v is None or str(v).strip() == "":
            return None
        return str(v).strip().upper()


def create_null_extraction(email_id: str) -> ShipmentExtraction:
    """Create an extraction with null/default values for failed emails."""
    return ShipmentExtraction(
        id=email_id,
        product_line="pl_sea_import_lcl",
        origin_port_code=None,
        origin_port_name=None,
        destination_port_code=None,
        destination_port_name=None,
        incoterm="FOB",
        cargo_weight_kg=None,
        cargo_cbm=None,
        is_dangerous=False,
    )
