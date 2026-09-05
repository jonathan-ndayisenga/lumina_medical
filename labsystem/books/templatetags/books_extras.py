from django import template

register = template.Library()


@register.filter
def startswith(value, prefix):
    return str(value).startswith(str(prefix))


@register.filter
def dictkey(mapping, key):
    return mapping.get(key)


@register.filter
def money(value):
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return value
