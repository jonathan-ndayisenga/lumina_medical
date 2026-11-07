import json

from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone

from accounts.models import AssistantQuestion

MAX_PER_MINUTE = 30


def run(request, assistant, app, log_hospital=None, **scope):
    """One question in, one answer out, as JSON. Remembers the last topic in the
    session so follow-ups work, and logs the question so gaps can be filled."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    key = f"assistant-rate:{request.user.pk}:{timezone.now():%Y%m%d%H%M}"
    if cache.get_or_set(key, 0, 70) >= MAX_PER_MINUTE:
        return JsonResponse({"text": "That's a lot of questions at once. Give me a minute.", "facts": [], "suggestions": [], "clarify": []})
    try:
        cache.incr(key)
    except ValueError:  # The minute rolled over between the two calls.
        cache.set(key, 1, 70)
    try:
        text = json.loads(request.body or b"{}").get("q", "")
    except (ValueError, AttributeError):
        text = ""
    text = str(text)[:300]
    session_key = f"assistant:{app}"
    result, context, topic = assistant.ask(text, request.session.get(session_key, {}), timezone.localdate(), **scope)
    request.session[session_key] = context
    if text.strip():
        AssistantQuestion.objects.create(
            user=request.user, app=app, hospital=log_hospital, text=text, topic=topic, answered=bool(topic) and topic not in ("clarify", "restricted"),
        )
    return JsonResponse(result)
