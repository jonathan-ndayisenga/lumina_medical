from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key."""
    return dictionary.get(key)


def _parse_bounds(reference_range):
    if not reference_range:
        return None
    cleaned = str(reference_range).strip().replace("–", "-").replace("—", "-")
    if "-" not in cleaned:
        return None
    lower_text, upper_text = [part.strip() for part in cleaned.split("-", 1)]
    try:
        return Decimal(lower_text), Decimal(upper_text)
    except (InvalidOperation, ValueError):
        return None


@register.filter
def range_flag(result_value, reference_range):
    bounds = _parse_bounds(reference_range)
    if not bounds:
        return ""
    try:
        value = Decimal(str(result_value).strip())
    except (InvalidOperation, ValueError):
        return ""

    lower, upper = bounds
    if value < lower:
        return "LOW"
    if value > upper:
        return "HIGH"
    return "NORMAL"
