"""Deterministic guard: every number in the agent's answer must come from a tool output."""
import re

NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|(?<![\w.])-?\d+(?:[.,]\d+)?")
DATE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b|\b\d{4}-\d{2}(?:-\d{2})?\b")


def numbers(text: str) -> list[float]:
    text = DATE.sub(" ", text)
    found = []
    for raw in NUMBER.findall(text):
        value = re.sub(r"[   ]", "", raw).replace(",", ".")
        try:
            found.append(float(value))
        except ValueError:
            pass
    return found


def check_numbers(answer: str, tool_outputs: list[str], tolerance: float = 0.01) -> list[float]:
    """Return numbers from the answer that no tool output supports (rounding tolerated)."""
    known = [n for out in tool_outputs for n in numbers(out)]
    # Small counters (steps, «3 позиции») and SKU-code fragments are not claims about data.
    claims = [n for n in numbers(answer) if abs(n) > 10]
    unsupported = []
    for n in claims:
        if not any(abs(n - k) <= max(tolerance * abs(k), 0.5) or abs(n - round(k)) < 0.5 for k in known):
            unsupported.append(n)
    return unsupported
