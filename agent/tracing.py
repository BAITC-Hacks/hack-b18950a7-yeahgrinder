"""Optional Langfuse tracing for the chat agent (Langfuse Python SDK v4).

One chat turn = one trace «chat-response»; one chat thread = one session. The LangGraph run
(model generations, tool calls) comes from the LangChain CallbackHandler; the deterministic
number check and the NVIDIA critic are separate guardrail/evaluator steps with trace scores.
No keys or Langfuse unreachable → the chat works untraced; tracing errors never break an answer.
"""
from contextlib import contextmanager
import logging
import os

from dotenv import load_dotenv

from engine.models import ROOT

load_dotenv(ROOT / ".env")  # before any Langfuse import: the client reads credentials from env
log = logging.getLogger(__name__)
TRACE_NAME = "chat-response"


def enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def environment() -> str:
    return os.environ.get("LANGFUSE_TRACING_ENVIRONMENT", "development")


def client():
    from langfuse import get_client
    return get_client()


class _Noop:
    def update(self, **kwargs):
        return self


class Turn:
    """Handle for one traced chat turn; every method is a no-op when tracing is off."""

    def __init__(self, span=None, callbacks=None):
        self.span, self.callbacks = span, callbacks or []

    @property
    def trace_id(self):
        return getattr(self.span, "trace_id", None)

    @contextmanager
    def step(self, name: str, as_type: str, input=None):
        """Child observation (e.g. guardrail/evaluator) nested under the turn."""
        if self.span is None:
            yield _Noop()
            return
        try:
            cm = self.span.start_as_current_observation(name=name, as_type=as_type, input=input)
            obs = cm.__enter__()
        except Exception as exc:
            log.warning("Langfuse: шаг %s без трассировки: %s", name, exc)
            yield _Noop()
            return
        try:
            yield obs
        finally:
            try:
                cm.__exit__(None, None, None)
            except Exception as exc:
                log.warning("Langfuse: не удалось закрыть шаг %s: %s", name, exc)

    def finish(self, reply: dict):
        if self.span is None:
            return
        try:
            # Trace output = what a reviewer needs at a glance: the assistant's answer.
            self.span.update(output=reply["answer"], metadata={
                "tools_used": reply["tools_used"], "numbers_ok": reply["numbers_ok"],
                "unsupported_numbers": reply["unsupported_numbers"], "critic_status": reply["critic"].get("status")})
            self.span.score_trace(name="numbers_grounded", value=1.0 if reply["numbers_ok"] else 0.0, data_type="BOOLEAN",
                                  comment=None if reply["numbers_ok"] else f"Не из данных: {reply['unsupported_numbers']}")
            status = reply["critic"].get("status")
            if status in {"passed", "review"}:
                self.span.score_trace(name="critic_verdict", value=status, data_type="CATEGORICAL",
                                      comment="; ".join(reply["critic"].get("issues", [])) or None)
        except Exception as exc:
            log.warning("Langfuse: не удалось записать итог хода: %s", exc)


@contextmanager
def traced_turn(user_id: str, session_id: str, run_id: str, question: str, metadata: dict | None = None):
    if not enabled():
        yield Turn()
        return
    try:
        from langfuse import propagate_attributes
        from langfuse.langchain import CallbackHandler
        meta = {"run_id": run_id, **(metadata or {})}
        span_cm = client().start_as_current_observation(as_type="agent", name=TRACE_NAME, input=question, metadata=meta)
        attrs_cm = propagate_attributes(user_id=user_id, session_id=session_id, trace_name=TRACE_NAME,
                                        tags=["feature:chat"], environment=environment(),
                                        metadata={"run_id": run_id})
        span = span_cm.__enter__()
        attrs_cm.__enter__()
        handler = CallbackHandler()
    except Exception as exc:
        log.warning("Langfuse недоступен, ход без трассировки: %s", exc)
        yield Turn()
        return
    try:
        yield Turn(span, [handler])
    finally:
        try:
            attrs_cm.__exit__(None, None, None)
            span_cm.__exit__(None, None, None)
        except Exception as exc:
            log.warning("Langfuse: не удалось закрыть трейс: %s", exc)


def flush():
    """Only for short-lived scripts; the API server exports in the background."""
    if enabled():
        try:
            client().flush()
        except Exception as exc:
            log.warning("Langfuse flush: %s", exc)


def trace_url(trace_id: str | None) -> str | None:
    if not (enabled() and trace_id):
        return None
    try:
        return client().get_trace_url(trace_id=trace_id)
    except Exception:
        return None


def status() -> dict:
    """Check keys against the server (setup check; not called on every request)."""
    if not enabled():
        return {"enabled": False, "reason": "нет LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY"}
    try:
        return {"enabled": True, "auth": client().auth_check(), "environment": environment()}
    except Exception as exc:
        return {"enabled": True, "auth": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}


