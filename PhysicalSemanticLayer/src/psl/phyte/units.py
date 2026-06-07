"""Unit handling for Phytes — thin wrapper around pint.

All physical quantities in PSL carry units. This module provides a
shared UnitRegistry and helpers for validation and conversion.
"""

from __future__ import annotations

import pint

# Single shared registry — all Phytes use this instance.
ureg: pint.UnitRegistry = pint.UnitRegistry()  # type: ignore[type-arg]
ureg.default_format = "~P"  # compact format

# Type alias for convenience
Unit = pint.Unit
Quantity = pint.Quantity


def parse_unit(unit_str: str) -> Unit:
    """Parse a unit string into a pint Unit.

    Args:
        unit_str: Unit string, e.g. 'm', 'rad', 'kg*m/s**2'.

    Returns:
        pint.Unit instance.

    Raises:
        pint.UndefinedUnitError: If the unit string is not recognized.
    """
    return ureg.Unit(unit_str)


def are_compatible(u1: Unit, u2: Unit) -> bool:
    """Check if two units are dimensionally compatible (e.g. m and mm)."""
    result: bool = ureg.Quantity(1, u1).is_compatible_with(ureg.Quantity(1, u2))
    return result


def convert_quantity(value: float, from_unit: Unit, to_unit: Unit) -> float:
    """Convert a scalar value between compatible units.

    Args:
        value: The numeric value.
        from_unit: Source unit.
        to_unit: Target unit.

    Returns:
        Converted numeric value.

    Raises:
        pint.DimensionalityError: If units are incompatible.
    """
    q = ureg.Quantity(value, from_unit)
    return float(q.to(to_unit).magnitude)


def dimensionality_str(unit: Unit) -> str:
    """Return the dimensionality string for a unit (e.g. '[length]')."""
    return str(ureg.Quantity(1, unit).dimensionality)
