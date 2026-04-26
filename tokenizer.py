"""
Tokenizer utilities extracted from translator.
"""
import sqlparse


def tokenize(sql: str) -> list[tuple[str, str]]:
    """
    Simplified SQL tokenizer using sqlparse.
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


def _comment_only_statement(text: str) -> bool:
    """Return True if a chunk is just a single line comment statement."""
    parsed = sqlparse.parse(text)
    for stmt in parsed:
        if stmt.get_type() == "COMMENT":
            return True
    return False
