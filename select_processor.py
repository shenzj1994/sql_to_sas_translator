"""SELECT handling and detection."""
import sqlparse


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
