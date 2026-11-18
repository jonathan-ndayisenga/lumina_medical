"""JSON for dropping straight into a <script> block with `|safe`.

Plain json.dumps leaves "<", ">" and "&" alone, so a doctor's name or a test
name containing "</script><script>..." would end the script tag and run as
code. These are escaped the same way Django's json_script filter does it; the
browser reads the same values back."""

import json

from django.core.serializers.json import DjangoJSONEncoder

_SCRIPT_ESCAPES = {ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026"}


def script_json(value) -> str:
    return json.dumps(value, cls=DjangoJSONEncoder).translate(_SCRIPT_ESCAPES)
