from langchain_core.messages import AIMessage

from agent import tracing
from agent.graph import ask
from engine.models import Params
from service import ProcurementService
from tests.test_agent import ScriptedModel


def scripted(qty):
    return ScriptedModel([
        AIMessage("", tool_calls=[{"name": "get_sku_explanation", "args": {"sku": "001_"}, "id": "c1"}]),
        AIMessage(f"Заказ {qty} шт."),
    ])


def run(dataset, tmp_path):
    service = ProcurementService(storage=tmp_path, dataset=dataset)
    result = service.calculate(Params())
    return service, result, int(result.orders.iloc[0].recommended_qty)


def test_chat_works_without_langfuse_keys(dataset, tmp_path, monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    service, result, qty = run(dataset, tmp_path)
    reply = ask(service, result.run_id, "Почему?", "t", model=scripted(qty), use_critic=False)
    assert not tracing.enabled()
    assert reply["numbers_ok"] and reply["trace_id"] is None


def test_unreachable_langfuse_never_breaks_chat(dataset, tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    service, result, qty = run(dataset, tmp_path)
    reply = ask(service, result.run_id, "Почему?", "t", model=scripted(qty), use_critic=False)
    assert reply["answer"] == f"Заказ {qty} шт." and reply["tools_used"] == ["get_sku_explanation"]
    assert reply["trace_id"]  # the turn was traced locally even though export will fail
