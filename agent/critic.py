"""NVIDIA-hosted independent critic. It reviews chat answers; it never changes quantities."""
import json
import re

from pydantic import BaseModel, Field, ValidationError

from agent.prompts import CRITIC_PROMPT
from agent.providers import critic_model


class Verdict(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


def parse_verdict(text: str) -> Verdict:
    # Not every catalog model supports structured output; take the first JSON object in the reply.
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("Критик не вернул JSON")
    return Verdict.model_validate(json.loads(match.group(0)))


def review(question: str, tool_outputs: list[str], answer: str, model=None) -> dict:
    """Return {"status": "passed"|"review"|"unavailable", ...}. Failure never blocks the answer."""
    model = model or critic_model()
    if model is None:
        return {"status": "unavailable", "issues": [], "reason": "NVIDIA critic выключен или нет ключа"}
    evidence = "\n".join(out[:4000] for out in tool_outputs[-6:]) or "(инструменты не вызывались)"
    messages = [("system", CRITIC_PROMPT),
                ("user", f"Вопрос:\n{question}\n\nРезультаты инструментов:\n{evidence}\n\nОтвет ассистента:\n{answer}")]
    try:
        verdict = parse_verdict(model.invoke(messages).content)
    except (ValueError, ValidationError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "issues": [], "reason": f"Ответ критика не разобран: {exc}"}
    except Exception as exc:  # network / quota errors must not break the chat
        return {"status": "unavailable", "issues": [], "reason": f"Критик недоступен: {type(exc).__name__}"}
    status = "passed" if verdict.passed and not verdict.requires_human_review else "review"
    return {"status": status, "issues": verdict.issues}
