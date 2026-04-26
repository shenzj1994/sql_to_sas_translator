"""Comment conversion helpers."""


def convert_comment_to_sas(kind: str, text: str) -> str:
    """Convert a standalone SQL comment token to a SAS /* … */ comment."""
    if kind == "line_comment":
        s = text.strip()
        body = s[2:].strip() if s.startswith("--") else s
        return f"/* {body} */" if body else "/* */"
    if kind == "block_comment":
        s = text.strip()
        return s if s.startswith("/*") and s.endswith("*/") else f"/* {s} */"

    s = text.strip()
    if s.startswith("--"):
        body = s[2:].strip()
        return f"/* {body} */" if body else "/* */"
    if s.startswith("/*") and s.endswith("*/"):
        return s
    return f"/* {s} */" if s else "/* */"


def _convert_inline_line_comments_to_sas(text: str) -> str:
    """Convert any -- comments that appear inside a SQL statement to SAS.

    This preserves the statement text while ensuring comment markers are SAS-safe.
    """
    lines = text.splitlines()
    converted: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("--"):
            indent_len = len(line) - len(stripped)
            indent = line[:indent_len]
            body = stripped[2:].strip()
            converted.append(f"{indent}/* {body} */" if body else f"{indent}/* */")
        else:
            converted.append(line)
    return "\n".join(converted)
