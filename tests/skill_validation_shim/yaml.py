"""Minimal flat-YAML compatibility shim for the upstream skill validator.

The JobOps runtime does not depend on YAML. This module is placed on PYTHONPATH
only while running skill-creator's quick_validate.py in an environment where
PyYAML is unavailable.
"""


class YAMLError(ValueError):
    pass


def safe_load(text: str):
    result = {}
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith((" ", "\t")) or ":" not in raw_line:
            raise YAMLError("Only flat key/value frontmatter is supported by this validation shim")
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in result:
            raise YAMLError("Missing or duplicate key")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result
