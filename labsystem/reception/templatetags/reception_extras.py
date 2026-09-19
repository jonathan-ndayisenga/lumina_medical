from django import template

register = template.Library()


@register.filter
def age_at(patient, when):
    """Patient's age as of a specific date/datetime -- for anything printed or
    reviewed after the fact (a receipt, a report, a past consultation), so
    reprinting/reviewing it later never shows today's age instead of the age
    that was actually true then. Use `patient.current_age` directly in a
    template for "right now" screens (queues, active visit creation) instead."""
    if not patient or not when:
        return ""
    return patient.age_at(when)
