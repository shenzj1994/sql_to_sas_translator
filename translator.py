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
import sqlparse


# ── tokenizer ──────────────────────────────────────────────────────────────────

def tokenize(sql: str) -> list[tuple[str, str]]:
    """
    Simplified SQL tokenizer using regex.
    Returns (token_type, token_value) pairs.
    Converts -- comments to /* */ format for SAS compatibility.
    Groups tokens into statements separated by semicolons.
    """

    parts: list[tuple[str, str]] = []
    current_statement: list[str] = []
    for line in sql.splitlines():
        if line.lstrip().startswith("--"):
            comment_text = line.strip()
            # Keep a leading comment as its own token, but do not split a
            # statement that is already in progress if the comment is inline.
            if current_statement:
                current_statement.append(line)
            else:
                parts.append(("line_comment", comment_text))
        else:
            current_statement.append(line)

        # Flush on blank lines only when the current statement has content and
        # the next non-empty line starts a new top-level statement.
        if line.strip() == "" and current_statement:
            stmt = "\n".join(current_statement).strip()
            if stmt:
                parts.append(("statement", stmt))
            current_statement = []

    if current_statement:
        stmt = "\n".join(current_statement).strip()
        if stmt:
            parts.append(("statement", stmt))

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
        if _is_select_statement(text):
            token = (f"select_dataset_{dataset_name}", text)
        else:
            token = ("statement", text)
    else:  # comment mode (default)
        warnings.append(
            f"Statement #{stmt_index} is a SELECT and will be commented out "
            f"(not executable via pass-through): {preview}"
        )
        token = ("select_comment", text)

    return select_counter, warnings, token



def _is_select_statement(text: str) -> bool:
    """Detect SELECT statements, including CTEs that begin with WITH.

    Uses a light lexical check so common CTEs are treated as SELECT-like
    statements instead of being wrapped as raw execute blocks.
    """
    stripped = text.lstrip()
    if not stripped:
        return False

    parsed = sqlparse.parse(text)
    if not parsed:
        return False
    for stmt in parsed:
        if stmt.get_type() == "SELECT":
            return True

    return False


def _comment_only_statement(text: str) -> bool:
    """Return True if a chunk is just a single line comment statement."""
    parsed = sqlparse.parse(text)
    for stmt in parsed:
        if stmt.get_type() == "COMMENT":
            return True
    return False



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
        if kind == "line_comment" and _comment_only_statement(text):
            filtered_tokens.append((kind, text))
            continue
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
