"""Stub connectors — API-like interface over synthetic business data."""

from __future__ import annotations

from mws.business.generator import generate_maintenance_data
from mws.business.schemas import CMWorkOrder, InventoryItem, SOPDocument


class CMStub:
    """Stub CMMS connector."""

    def __init__(self, seed: int = 0) -> None:
        data = generate_maintenance_data(seed)
        self._work_orders: list[CMWorkOrder] = data["work_orders"]

    def get_open_orders(self, equipment_id: str) -> list[CMWorkOrder]:
        return [
            wo
            for wo in self._work_orders
            if wo.equipment_id == equipment_id and wo.status == "open"
        ]


class WMStub:
    """Stub WMS connector."""

    def __init__(self, seed: int = 0) -> None:
        data = generate_maintenance_data(seed)
        self._inventory: list[InventoryItem] = data["inventory"]

    def check_stock(self, item_id: str) -> InventoryItem | None:
        for item in self._inventory:
            if item.item_id == item_id:
                return item
        return None


class SOPStub:
    """Stub SOP document store."""

    def __init__(self, seed: int = 0) -> None:
        data = generate_maintenance_data(seed)
        self._sops: list[SOPDocument] = data["sops"]

    def find_by_equipment(self, equipment_type: str) -> list[SOPDocument]:
        return [s for s in self._sops if s.equipment_type == equipment_type]
