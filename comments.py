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
    lines = text.splitlines(keepends=True)
    converted: list[str] = []
    for line in lines:
        line_ending = ""
        if line.endswith("\r\n"):
            line_body = line[:-2]
            line_ending = "\r\n"
        elif line.endswith("\n"):
            line_body = line[:-1]
            line_ending = "\n"
        else:
            line_body = line

        comment_start = line_body.find("--")
        if comment_start == -1:
            converted.append(line)
            continue

        prefix = line_body[:comment_start]
        body = line_body[comment_start + 2:].strip()
        converted.append(f"{prefix}/* {body} */" if body else f"{prefix}/* */")
        if line_ending:
            converted.append(line_ending)

    return "".join(converted)
