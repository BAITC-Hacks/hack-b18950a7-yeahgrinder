"""Guards around the chat agent: read-only SQL, untrusted text, tool allowlist."""
import re

ALLOWED_TOOLS = {"get_run_summary", "list_orders", "get_sku_explanation", "get_schema",
                 "query_data", "what_if", "propose_rule"}
MAX_AGENT_STEPS = 6
SQL_LIMIT = 100
FORBIDDEN_SQL = re.compile(r"\b(attach|detach|copy|install|load|pragma|export|import|create|insert|update|"
                           r"delete|drop|alter|set|reset|call|checkpoint|vacuum|use)\b|read_\w+|;", re.I)


class UnsafeQuery(ValueError):
    pass


def safe_select(sql: str) -> str:
    """Accept a single SELECT/WITH statement and cap its size; everything else is rejected."""
    query = sql.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", query, re.I):
        raise UnsafeQuery("Разрешены только запросы SELECT")
    if FORBIDDEN_SQL.search(query):
        raise UnsafeQuery("Запрос содержит запрещённую операцию")
    return f"SELECT * FROM ({query}) AS q LIMIT {SQL_LIMIT}"


def wrap_untrusted(value) -> str:
    # Product names come from partner files; the model must treat them as data only.
    text = str(value).replace("<", "‹").replace(">", "›")
    return f"<untrusted_data>{text}</untrusted_data>"
