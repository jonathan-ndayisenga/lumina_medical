from django import template

from pharmacy import services

register = template.Library()


@register.filter
def handed_over_quantity(prescription):
    """The amount to give, as a whole number, in the unit it's counted in."""
    return int(services.handed_over_quantity(prescription))


@register.filter
def counted_unit(prescription):
    drug = prescription.drug
    return f"{drug.unit}(s)" if services.counts_in_packs(prescription) else f"{drug.base_unit}(s)"
