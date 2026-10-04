"""Turns the eye-exam forms' POST data into stored records and back into
template-ready rows. Field names are fixed here so the form, the saved data
and the summary all agree."""

from .constants import EYES, REFRACTION_METHODS, SLIT_LAMP_SECTIONS
from .models import BaseRefraction, EyeExam

EYE_KEYS = [eye for eye, _ in EYES]


def base_refraction_field_names():
    names = ["oriented", "mood", "mood_description", "neuro_notes", "pupils_perrl", "add_power", "pd_right", "pd_left"]
    for eye in EYE_KEYS:
        names += [f"va_distance_ph_{eye}", f"va_distance_cc_{eye}", f"va_near_cc_{eye}", f"va_best_{eye}"]
        names += [f"pupil_{eye}_{part}" for part in ("dark", "light", "shape", "react", "apd")]
        names += [f"k_{eye}_{part}" for part in ("k1", "k2", "axis")]
        for method, _ in REFRACTION_METHODS:
            names += [f"rx_{method}_{eye}_{part}" for part in ("sphere", "cylinder", "axis")]
    return names


def save_base_refraction(visit, post, user):
    data = {name: post.get(name, "").strip() for name in base_refraction_field_names()}
    data["oriented"] = "yes" if post.get("oriented") else ""
    data["pupils_perrl"] = "yes" if post.get("pupils_perrl") else ""
    record, _ = BaseRefraction.objects.get_or_create(visit=visit, defaults={"recorded_by": user})
    record.data = data
    record.comments = post.get("comments", "").strip()
    record.recorded_by = user
    record.save()
    return record


def save_eye_exam(visit, post, user):
    slit_lamp = {}
    for key, _, options in SLIT_LAMP_SECTIONS:
        section = {}
        for eye in EYE_KEYS:
            section[eye] = [value for value in post.getlist(f"sl_{key}_{eye}") if value in options]
            section[f"{eye}_other"] = post.get(f"sl_{key}_{eye}_other", "").strip()
        slit_lamp[key] = section
    exam, _ = EyeExam.objects.get_or_create(visit=visit, defaults={"examined_by": user})
    exam.external_right = post.get("external_right", "").strip()
    exam.external_left = post.get("external_left", "").strip()
    exam.slit_lamp = slit_lamp
    for name in ("cdr_right", "cdr_left", "iop_right", "iop_left"):
        setattr(exam, name, post.get(name, "").strip())
    exam.diagnoses = [d.strip() for d in post.getlist("eye_diagnosis") if d.strip()]
    exam.history_comments = post.get("history_comments", "").strip()
    exam.management_plan = post.get("management_plan", "").strip()
    exam.examined_by = user
    exam.save()
    return exam


def slit_lamp_rows(exam=None, post=None):
    """One row per section with each eye's options marked checked, for the
    form (from POST after an error, else the saved exam) and the summary."""
    saved = exam.slit_lamp if exam else {}
    rows = []
    for key, label, options in SLIT_LAMP_SECTIONS:
        eyes = []
        for eye, eye_label in EYES:
            if post is not None:
                checked = set(post.getlist(f"sl_{key}_{eye}"))
                other = post.get(f"sl_{key}_{eye}_other", "")
            else:
                checked = set(saved.get(key, {}).get(eye, []))
                other = saved.get(key, {}).get(f"{eye}_other", "")
            eyes.append({
                "eye": eye, "label": eye_label, "name": f"sl_{key}_{eye}", "other": other,
                "options": [{"value": option, "checked": option in checked} for option in options],
                "findings": [option for option in options if option in checked] + ([other] if other else []),
            })
        rows.append({"key": key, "label": label, "eyes": eyes})
    return rows
