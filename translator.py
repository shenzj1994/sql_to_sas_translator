"""
SQL-to-SAS Translator
─────────────────────
Core module for converting SQL queries into SAS PROC SQL pass-through syntax.
Handles tokenization, syntax translation, and validation with configurable
SELECT statement handling modes.

Public API
──────────
        translate(
        sql: str,
        conn_name: str = "myconn",
        conn_dbtype: str = "oracle",
        conn_dsn: str = "",
        conn_authdomain: str = "",
        conn_type: str = "global",
        select_mode: str = "comment"  # "ignore"|"comment"|"dataset"
    ) -> dict:
        Returns:
            {
                "sas": str,          # Generated SAS code
                "warnings": list[str],
                "counts": {
                    "total": int,    # Total statements processed
                    "wrapped": int,  # Statements wrapped as datasets
                    "skipped": int,  # Statements skipped (if select_mode="ignore")
                    "selected": int  # Statements that are SELECT type
                }
            }

Features
────────
- Supports all major SQL clauses (SELECT, FROM, WHERE, etc.)
- Configurable SELECT handling:
  - "ignore": Skips SELECT statements
  - "comment": Converts to SAS-commented blocks (default)
  - "dataset": Wraps as SAS datasets via PROC SQL
- Connection string validation
- Detailed warning reporting

Example
───────
>>> translate(
...     "SELECT * FROM table WHERE id = 1",
...     conn_name="prod",
...     conn_dbtype="oracle",
...     select_mode="dataset"
... )
"""

import re


# ── tokenizer ──────────────────────────────────────────────────────────────────

def tokenize(sql: str, use_sqlparse: bool = True) -> list[tuple[str, str]]:
    """
    Simplified SQL tokenizer using regex.
    Returns (token_type, token_value) pairs.
    Converts -- comments to /* */ format for SAS compatibility.
    Groups tokens into statements separated by semicolons.
    """

    # Prefer sqlparse when available to split and identify statements.
    if use_sqlparse:
        try:
            import sqlparse
            parts_out: list[tuple[str, str]] = []
            source = sqlparse.format(
                sql,
                reindent=True,
                keyword_case='upper',
                strip_comments=False,
            )
            for stmt in sqlparse.split(source):
                s = stmt if not stmt.endswith(';') else stmt[:-1]
                if not s.strip():
                    continue
                parts_out.extend(_split_sqlparse_segment(s))
            return parts_out
        except Exception:
            # fall through to fallback
            pass

    # Simpler tokenizer fallback: split by semicolons and treat leading
    # line comments in each segment as separate comment tokens. This avoids
    # the regex alternation edge-case where a comment and following
    # statement could be captured together.
    parts = []
    # First, handle block comments by extracting them and replacing with
    # placeholders to avoid accidental merging. We'll keep them inline.
    # Split on semicolons to get statement-like chunks.
    segments = sql.split(';')
    for seg in segments:
        if not seg or not seg.strip():
            continue
        # Process block comments that may span multiple lines inside the segment
        # If a segment is solely a block comment, return it as such.
        seg_stripped = seg
        if seg_stripped.lstrip().startswith('/*') and '*/' in seg_stripped:
            parts.append(('block_comment', seg_stripped.strip()))
            continue

        # Now handle line-oriented comments and statements within the segment
        lines = seg_stripped.splitlines()
        stmt_lines: list[str] = []
        for line in lines:
            if line.lstrip().startswith('--'):
                if stmt_lines:
                    stmt = "\n".join(stmt_lines).strip()
                    if stmt:
                        parts.extend(_split_leading_comments_and_statement(stmt))
                    stmt_lines = []
                parts.append(('line_comment', line.strip()))
            else:
                stmt_lines.append(line)

        if stmt_lines:
            stmt = "\n".join(stmt_lines)
            s_lines = stmt.splitlines()
            first_non_empty = 0
            for i, l in enumerate(s_lines):
                if l.strip():
                    first_non_empty = i
                    break
            stmt = "\n".join(s_lines[first_non_empty:]).strip()
            if stmt:
                parts.extend(_split_leading_comments_and_statement(stmt))

    return parts


# ── comment conversion ─────────────────────────────────────────────────────────

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
    if s.startswith("/*") and s.endswith("*/"):
        return s
    if s.startswith("--"):
        body = s[2:].strip()
        return f"/* {body} */" if body else "/* */"
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


def _split_sqlparse_segment(text: str) -> list[tuple[str, str]]:
    """Split a sqlparse segment into comment + statement tokens.

    This keeps standalone comments as comment tokens, but preserves inline
    comments inside statements so they can be converted later before wrapping.
    """
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("--"):
        return [('line_comment', stripped.splitlines()[0].strip())]
    if stripped.startswith("/*") and stripped.endswith("*/"):
        return [('block_comment', stripped)]
    return [('statement', _strip_leading_blank_lines_before_select(text))]


# ── statement formatting ───────────────────────────────────────────────────────

def normalize_whitespace(stmt: str) -> str:
    """Normalize whitespace in a statement."""
    stmt = re.sub(r"\n{3,}", "\n\n", stmt)
    lines = [line.rstrip() for line in stmt.splitlines()]
    return "\n".join(lines).strip()


def indent(text: str, spaces: int = 4) -> str:
    """Indent text by the specified number of spaces."""
    pad = " " * spaces
    return "\n".join(
        pad + line if line.strip() else line for line in text.splitlines()
    )


def wrap_as_execute(stmt: str, conn: str) -> str:
    """Wrap a SQL statement as a SAS execute block."""
    # Do not attempt to reformat SQL; preserve statement as provided by user.
    body = indent(_convert_inline_line_comments_to_sas(stmt), spaces=8)
    return f"    execute (\n{body}\n    ) by {conn};"


def wrap_as_sas_comment(stmt: str) -> str:
    """Render a SQL statement as a SAS block comment instead of executable code."""
    body = normalize_whitespace(stmt)
    body = body.replace("/*", "/ *").replace("*/", "* /")
    body = indent(body, spaces=8)
    return f"    /*\n{body}\n    */"


def wrap_select_as_dataset(stmt: str, dataset_name: str, conn: str) -> str:
    """Wrap a SELECT statement to create a dataset using pass-through syntax."""
    # Preserve original SELECT text without reformatting
    body = indent(_convert_inline_line_comments_to_sas(stmt), spaces=16)
    return (
        f"    create table {dataset_name} as\n"
        f"        select *\n"
        f"            from connection to {conn}\n"
        f"                (\n{body}\n"
        f"                );"
    )


# ── SAS block assembly ─────────────────────────────────────────────────────────

def build_sas_block(
    tokens: list[tuple[str, str]],
    conn_name: str,
    conn_string: str,
    wrapped_count: int,
) -> str:
    header = (
        f"/* Generated by sql-to-sas-web\n"
        f"   Connection      : {conn_name}\n"
        f"   Stmts wrapped   : {wrapped_count}\n"
        f"*/"
    )

    body_parts: list[str] = []
    pending_comments: list[str] = []

    for kind, text in tokens:
        if kind in ("line_comment", "block_comment"):
            pending_comments.append("    " + convert_comment_to_sas(kind, text))
        else:
            if pending_comments:
                body_parts.append("\n".join(pending_comments))
                pending_comments = []
            if kind == "select_comment":
                body_parts.append(wrap_as_sas_comment(text))
            elif kind.startswith("select_dataset_"):
                dataset_name = kind.split("_", 2)[2]
                body_parts.append(wrap_select_as_dataset(text, dataset_name, conn_name))
            else:
                body_parts.append(wrap_as_execute(text, conn_name))

    if pending_comments:
        body_parts.append("\n".join(pending_comments))

    body = "\n\n".join(body_parts)
    return (
        f"{header}\n\n"
        f"proc sql;\n"
        f"    {conn_string}\n\n"
        f"{body}\n\n"
        f"    disconnect from {conn_name};\n"
        f"quit;"
    )


# ── connection string builder ──────────────────────────────────────────────────

def build_connection_string(
    conn_name: str, conn_dbtype: str, conn_dsn: str,
    conn_authdomain: str, conn_type: str
) -> str:
    """Build a SAS connection string from parameters."""
    conn_parts = [conn_dbtype, "as", conn_name]
    conn_opts = []

    if conn_dsn:
        conn_opts.append(f"dsn='{conn_dsn}'")
    if conn_authdomain:
        conn_opts.append(f"authdomain='{conn_authdomain}'")
    if conn_type:
        conn_opts.append(f"connection={conn_type}")

    if conn_opts:
        return f"connect to {' '.join(conn_parts)} ({' '.join(conn_opts)});"
    return f"connect to {' '.join(conn_parts)};"


def process_select_statement(
    stmt_index: int, text: str, select_mode: str, select_counter: int
) -> tuple[int, list[str], tuple[str, str] | None]:
    """Process a SELECT statement based on the mode."""
    preview = " ".join(text.split()[:12])
    if len(text.split()) > 12:
        preview += " …"

    warnings = []
    token = None

    if select_mode == "ignore":
        warnings.append(
            f"Statement #{stmt_index} is a SELECT and will be ignored: {preview}"
        )
    elif select_mode == "dataset":
        select_counter += 1
        dataset_name = f"select_{select_counter}"
        warnings.append(
            f"Statement #{stmt_index} is a SELECT and will create "
            f"dataset '{dataset_name}': {preview}"
        )
        # Remove any leading empty lines before the SELECT to avoid
        # emitting multiple blank lines in the generated SAS.
        text_stripped = _strip_leading_blank_lines_before_select(text)
        token = (f"select_dataset_{dataset_name}", text_stripped)
    else:  # comment mode (default)
        warnings.append(
            f"Statement #{stmt_index} is a SELECT and will be commented out "
            f"(not executable via pass-through): {preview}"
        )
        text_stripped = _strip_leading_blank_lines_before_select(text)
        token = ("select_comment", text_stripped)

    return select_counter, warnings, token


def _strip_leading_blank_lines_before_select(text: str) -> str:
    """If the first non-empty line of the statement is a SELECT, remove any
    leading empty lines so the generated SAS does not include excessive gaps.
    """
    if not text:
        return text
    lines = text.splitlines()
    # find first non-empty line index
    first_non_empty = 0
    for i, line in enumerate(lines):
        if line.strip():
            first_non_empty = i
            break
    # If that line starts with SELECT, drop all preceding blank lines
    first_line = lines[first_non_empty] if lines else ""
    if first_line.lstrip().upper().startswith("SELECT"):
        return "\n".join(lines[first_non_empty:])
    return text


def _is_select_statement(text: str) -> bool:
    """Detect SELECT statements, including CTEs that begin with WITH.

    Uses a light lexical check so common CTEs are treated as SELECT-like
    statements instead of being wrapped as raw execute blocks.
    """
    stripped = text.lstrip()
    if not stripped:
        return False
    upper = stripped.upper()
    if upper.startswith("SELECT"):
        return True
    if not upper.startswith("WITH"):
        return False
    # Walk through balanced parentheses of CTEs, then look for SELECT.
    depth = 0
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and upper[i:i+6] == "SELECT":
            return True
        i += 1
    return False


def _split_leading_comments_and_statement(text: str) -> list[tuple[str, str]]:
    """Split a chunk into leading comments plus the remaining statement.

    Used in the fallback path to prevent comments from being glued to the next
    statement when blank lines are present.
    """
    out: list[tuple[str, str]] = []
    remaining = text.lstrip()
    while remaining.startswith('--'):
        line_end = remaining.find('\n')
        if line_end == -1:
            out.append(('line_comment', remaining.strip()))
            return out
        out.append(('line_comment', remaining[:line_end].strip()))
        remaining = remaining[line_end + 1 :].lstrip('\n')
    if remaining.strip():
        out.append(('statement', remaining))
    return out


# ── public API ─────────────────────────────────────────────────────────────────

def translate(
    sql: str, conn_name: str = "myconn", conn_dbtype: str = "oracle",
    conn_dsn: str = "", conn_authdomain: str = "", conn_type: str = "global",
    select_mode: str = "comment",
    use_sqlparse: bool = True
) -> dict:
    """
    Translate a SQL string into a SAS PROC SQL pass-through block.

    Parameters
    ----------
    sql             : raw SQL source text
    conn_name       : SAS connection/libname alias (e.g., 'myconn')
    conn_dbtype     : database type (e.g., 'oracle')
    conn_dsn        : DSN name (e.g., 'ABC_DSN')
    conn_authdomain : authentication domain (e.g., 'my_authdomain')
    conn_type       : connection type (e.g., 'global')
    select_mode     : how to handle SELECT statements: "ignore", "comment", or "dataset"

    Returns
    -------
    {
        "sas":      str,
        "warnings": list[str],
        "counts":   {"total": int, "wrapped": int, "skipped": int, "selected": int},
    }

    SELECT statements are handled according to select_mode:
    - "ignore": completely omitted from output
    - "comment": preserved as SAS comments
    - "dataset": executed and create datasets (SELECT_1, SELECT_2, etc.)
    """
    conn_string = build_connection_string(
        conn_name, conn_dbtype, conn_dsn, conn_authdomain, conn_type
    )

    tokens = tokenize(sql, use_sqlparse=use_sqlparse)
    filtered_tokens: list[tuple[str, str]] = []
    warnings: list[str] = []
    total = 0
    skipped = 0
    selected = 0
    select_counter = 0
    stmt_index = 0

    for kind, text in tokens:
        if kind != "statement":
            filtered_tokens.append((kind, text))
            continue

        total += 1
        stmt_index += 1
        if _is_select_statement(text):
            selected += 1
            select_counter, stmt_warnings, token = process_select_statement(
                stmt_index, text, select_mode, select_counter
            )
            warnings.extend(stmt_warnings)
            if select_mode == "ignore":
                skipped += 1
            elif token:
                filtered_tokens.append(token)
        else:
            filtered_tokens.append((kind, text))

    wrapped = total - (skipped if select_mode != "dataset" else 0)
    sas = build_sas_block(
        filtered_tokens, conn_name=conn_name, conn_string=conn_string,
        wrapped_count=wrapped
    )

    return {
        "sas": sas,
        "warnings": warnings,
        "counts": {
            "total": total, "wrapped": wrapped, "skipped": skipped,
            "selected": selected
        },
    }
