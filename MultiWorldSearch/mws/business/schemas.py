"""Business record schemas — synthetic CMMS, WMS, ERP, SOP data."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CMWorkOrder(BaseModel):
    """CMMS work order record."""

    work_order_id: str
    equipment_id: str
    description: str
    priority: int = Field(ge=1, le=5)
    status: str = "open"  # open, in_progress, closed
    assigned_to: str = ""
    created_at: float = 0.0
    completed_at: float | None = None


class InventoryItem(BaseModel):
    """WMS inventory record."""

    item_id: str
    name: str
    location: str
    quantity: int
    unit: str = "pcs"
    min_stock: int = 0


class SOPDocument(BaseModel):
    """Standard Operating Procedure document."""

    sop_id: str
    title: str
    equipment_type: str
    content: str
    revision: str = "1.0"
    safety_notes: list[str] = Field(default_factory=list)


class MaintenanceLog(BaseModel):
    """Historical maintenance log entry."""

    log_id: str
    equipment_id: str
    action: str
    technician: str
    timestamp: float
    notes: str = ""
    parts_used: list[str] = Field(default_factory=list)
