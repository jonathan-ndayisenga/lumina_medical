from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.models import User
from doctor.views import doctor_role_required, get_active_hospital
from reception.models import Visit

from .constants import APD_GRADES, DISTANCE_VA, EYES, MOODS, NEAR_VA, PUPIL_REACTIONS, PUPIL_SHAPES, REFRACTION_METHODS
from .exam import save_base_refraction


@doctor_role_required
def base_refraction(request, visit_id):
    hospital = get_active_hospital(request)
    visits = Visit.objects.select_related("patient", "hospital")
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        visits = visits.filter(hospital=hospital)
    visit = get_object_or_404(visits, pk=visit_id)
    if visit.status == Visit.STATUS_CANCELLED:
        messages.error(request, "This visit was terminated by an administrator and can no longer be edited.")
        return redirect("doctor_queue")

    record = getattr(visit, "base_refraction", None)
    if request.method == "POST":
        save_base_refraction(visit, request.POST, request.user)
        messages.success(request, f"Base refraction saved for {visit.patient.name}. Continue with the main exam.")
        return redirect("consultation", visit_id=visit.pk)

    data = record.data if record else {}

    def cells(name_for_eye, options=None):
        return [{"name": name_for_eye(eye), "value": data.get(name_for_eye(eye), ""), "options": options} for eye, _ in EYES]

    va_rows = [
        {"label": "Distance, pinhole (PH)", "cells": cells(lambda e: f"va_distance_ph_{e}", DISTANCE_VA)},
        {"label": "Distance, with correction (CC)", "cells": cells(lambda e: f"va_distance_cc_{e}", DISTANCE_VA)},
        {"label": "Near, with correction (CC)", "cells": cells(lambda e: f"va_near_cc_{e}", NEAR_VA)},
        {"label": "Best vision", "cells": cells(lambda e: f"va_best_{e}", DISTANCE_VA)},
    ]
    pupil_rows = [
        {"label": "Dark (mm)", "cells": cells(lambda e: f"pupil_{e}_dark")},
        {"label": "Light (mm)", "cells": cells(lambda e: f"pupil_{e}_light")},
        {"label": "Shape", "cells": cells(lambda e: f"pupil_{e}_shape", PUPIL_SHAPES)},
        {"label": "Reaction", "cells": cells(lambda e: f"pupil_{e}_react", PUPIL_REACTIONS)},
        {"label": "APD", "cells": cells(lambda e: f"pupil_{e}_apd", APD_GRADES)},
    ]
    refraction_blocks = [
        {"label": method_label, "rows": [
            {"label": part.title(), "cells": cells(lambda e, m=method, pt=part: f"rx_{m}_{e}_{pt}")}
            for part in ("sphere", "cylinder", "axis")
        ]}
        for method, method_label in REFRACTION_METHODS
    ]
    keratometry_rows = [
        {"label": label, "cells": cells(lambda e, pt=part: f"k_{e}_{pt}")}
        for part, label in (("k1", "K1"), ("k2", "K2"), ("axis", "Axis"))
    ]
    return render(request, "ophthalmology/base_refraction.html", {
        "active_nav": "doctor",
        "visit": visit,
        "data": data,
        "comments": record.comments if record else "",
        "eyes": EYES,
        "moods": MOODS,
        "va_rows": va_rows,
        "pupil_rows": pupil_rows,
        "refraction_blocks": refraction_blocks,
        "keratometry_rows": keratometry_rows,
        "skip_url": reverse("consultation", args=[visit.pk]) + "?skip_refraction=1",
    })
