import json

from langchain_core.messages import AIMessage
import pytest

from agent.critic import parse_verdict, review
from agent.graph import ask
from agent.numbers_check import check_numbers
from agent.security import UnsafeQuery, safe_select
from agent.tools import make_tools
from engine.models import Params
from service import ProcurementService


class ScriptedModel:
    """Stands in for a chat model: replays prepared AI messages, records what it was shown."""
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen.append(messages)
        return self.replies.pop(0)


class StaticModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return AIMessage(self.content)


@pytest.fixture
def run(dataset, tmp_path):
    service = ProcurementService(storage=tmp_path, dataset=dataset)
    return service, service.calculate(Params())


def test_sql_guard():
    assert safe_select("select sku from orders").endswith("LIMIT 100")
    for bad in ["drop table orders", "select 1; drop table x", "select * from read_csv('/etc/hosts')",
                "with a as (select 1) insert into t select * from a", "attach 'x.db'"]:
        with pytest.raises(UnsafeQuery):
            safe_select(bad)


def test_tools_return_engine_numbers(run):
    service, result = run
    tools = {t.name: t for t in make_tools(service, result.run_id)}
    qty = int(result.orders.iloc[0].recommended_qty)
    explanation = json.loads(tools["get_sku_explanation"].invoke({"sku": "001_"}))
    assert explanation["recommended_qty"] == qty and explanation["reason_text"]
    rows = json.loads(tools["query_data"].invoke({"sql": "select supplier, recommended_qty from orders"}))["rows"]
    assert len(rows) == 2
    assert "error" in json.loads(tools["query_data"].invoke({"sql": "delete from orders"}))
    proposal = json.loads(tools["propose_rule"].invoke({"sku": "001_", "min_qty": 100}))
    assert proposal["saved"] is False and service.memory.rules("local") == {}


def test_agent_uses_tools_and_numbers_are_checked(run):
    service, result = run
    qty = int(result.orders[result.orders.supplier == "IEK"].iloc[0].recommended_qty)
    model = ScriptedModel([
        AIMessage("", tool_calls=[{"name": "get_sku_explanation", "args": {"sku": "001_"}, "id": "c1"}]),
        AIMessage(f"Заказ {qty} шт. — остатка нет. Проверил также 987654 шт."),
    ])
    reply = ask(service, result.run_id, "Почему такой заказ?", "t1", model=model, use_critic=False)
    assert reply["tools_used"] == ["get_sku_explanation"]
    assert reply["unsupported_numbers"] == [987654.0]  # the made-up number is caught, the real one is not


def test_disallowed_tool_is_refused(run):
    service, result = run
    model = ScriptedModel([AIMessage("", tool_calls=[{"name": "send_order", "args": {}, "id": "x"}])])
    reply = ask(service, result.run_id, "Отправь заказ", "t2", model=model, use_critic=False)
    assert reply["tools_used"] == ["send_order"] and "Не могу" in reply["answer"]


def test_critic_is_optional_and_parsed():
    assert review("q", [], "a", model=None)["status"] in {"unavailable", "passed", "review"}
    verdict = review("q", ["{}"], "a", model=StaticModel('Итог: {"passed": false, "issues": ["нет транзита"]}'))
    assert verdict == {"status": "review", "issues": ["нет транзита"]}
    assert parse_verdict('{"passed": true}').passed
    assert review("q", [], "a", model=StaticModel("не JSON"))["status"] == "unavailable"


def test_number_check_accepts_rounding_and_spaces():
    assert check_numbers("Заказ 1 440 шт., база 312 шт./мес", ['{"qty": 1440, "base": 311.8}']) == []
