"""Daily document numbers such as TH-RCT-20261118-0007.

The next number is one more than the highest number already used with that
prefix, worked out as a number. Counting rows reused a number after a delete,
and sorting the text put "...-10000" below "...-9999". If two saves race for
the same number, the database's unique constraint rejects the second, and it
retries with the next number.
"""

from django.db import IntegrityError, transaction

ATTEMPTS = 5


def next_number(model, field, prefix, padding=4):
    values = model.objects.filter(**{f"{field}__startswith": prefix}).values_list(field, flat=True)
    highest = 0
    for value in values:
        suffix = value[len(prefix):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:0{padding}d}"


def save_with_number(instance, field, prefix, save, padding=4):
    """Give `instance` the next number under `prefix`, then call `save()`
    (the model's real save). The model's `field` must be unique."""
    model = type(instance)
    for attempt in range(ATTEMPTS):
        setattr(instance, field, next_number(model, field, prefix, padding))
        try:
            with transaction.atomic():
                return save()
        except IntegrityError:
            taken = model.objects.filter(**{field: getattr(instance, field)}).exists()
            if attempt == ATTEMPTS - 1 or not taken:
                raise
