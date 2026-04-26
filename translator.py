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

def tokenize(sql: str) -> list[tuple[str, str]]:
    """
    Simplified SQL tokenizer using regex.
    Returns (token_type, token_value) pairs.
    Converts -- comments to /* */ format for SAS compatibility.
    Groups tokens into statements separated by semicolons.
    """

    # Simpler tokenizer: don't try to reformat SQL. Preserve original
    # statements and comments in order and return minimal token kinds.
    parts = []
    # Pattern matches block comments, line comments, or any chunk up to a semicolon
    splitter = re.compile(r"(/\*.*?\*/|--.*?$|[^;]+;?)", re.DOTALL | re.MULTILINE)
    for m in splitter.finditer(sql):
        part = m.group(0)
        if not part:
            continue
        part_stripped = part
        if part_stripped.startswith('/*') and '*/' in part_stripped:
            parts.append(('block_comment', part_stripped))
        elif part_stripped.lstrip().startswith('--'):
            # convert SQL line comment to a line_comment token; conversion to
            # SAS-style is handled later by convert_comment_to_sas
            parts.append(('line_comment', part_stripped.strip()))
        else:
            # It's a statement or statement fragment; strip a trailing semicolon
            # and remove leading blank lines so single statements don't emit
            # excessive empty space in generated SAS.
            stmt = part_stripped
            if stmt.endswith(';'):
                stmt = stmt[:-1]
            # Remove leading blank lines (lines that are empty or whitespace)
            lines = stmt.splitlines()
            first_non_empty = 0
            for i, line in enumerate(lines):
                if line.strip():
                    first_non_empty = i
                    break
            stmt = "\n".join(lines[first_non_empty:])
            if stmt.strip():
                parts.append(('statement', stmt))

    return parts


# ── comment conversion ─────────────────────────────────────────────────────────

def convert_comment_to_sas(kind: str, text: str) -> str:
    """Convert a standalone SQL comment token to a SAS /* … */ comment."""
    # Preserve block comments as-is
    if kind == "block_comment":
        return text

    # Normalize line comments (e.g. -- comment) into SAS /* ... */
    s = text.strip()
    if s.startswith('/*') and s.endswith('*/'):
        return s
    if s.startswith('--'):
        body = s[2:].strip()
    else:
        # Fallback: remove leading dashes or whitespace
        body = s.lstrip('-').strip()
    return f"/* {body} */" if body else "/* */"


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
    body = indent(stmt, spaces=8)
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
    body = indent(stmt, spaces=16)
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


# ── public API ─────────────────────────────────────────────────────────────────

def translate(
    sql: str, conn_name: str = "myconn", conn_dbtype: str = "oracle",
    conn_dsn: str = "", conn_authdomain: str = "", conn_type: str = "global",
    select_mode: str = "comment"
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

    tokens = tokenize(sql)
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
        first_word = text.split()[0].upper() if text.split() else ""

        if first_word == "SELECT":
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
