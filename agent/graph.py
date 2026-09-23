"""Chat agent over a finished run: LangGraph loop agent ⇄ tools with an allowlist and a step limit."""
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, trim_messages
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from agent.critic import review
from agent.numbers_check import check_numbers
from agent.prompts import EXPLAINER_PROMPT, SYSTEM_PROMPT
from agent.providers import chat_model
from agent.security import ALLOWED_TOOLS, MAX_AGENT_STEPS
from agent.tools import make_tools
from agent.tracing import traced_turn

CHECKPOINTER = InMemorySaver()  # short-term memory, keyed by thread_id


class AgentUnavailable(RuntimeError):
    pass


class ChatState(MessagesState):
    steps: int


def route(state: ChatState):
    last = state["messages"][-1]
    calls = getattr(last, "tool_calls", None) or []
    if not calls:
        return END
    if state.get("steps", 0) >= MAX_AGENT_STEPS or any(c["name"] not in ALLOWED_TOOLS for c in calls):
        return "refuse-tool-call"
    return "run-tools"


def build_agent(service, run_id: str, user_id: str = "local", model=None):
    model = model or chat_model()
    if model is None:
        raise AgentUnavailable("Чат выключен: не задан OPENAI_API_KEY в .env. Расчёт и экспорт работают без него.")
    tools = make_tools(service, run_id)
    bound = model.bind_tools(tools)
    params = service.get_result(run_id).params.model_dump(mode="json")
    system = SYSTEM_PROMPT.format(params=json.dumps(params, ensure_ascii=False),
                                  preferences=json.dumps(service.memory.preferences(user_id), ensure_ascii=False))

    def agent(state: ChatState):
        # Last 12 messages go to the model; the full dialogue stays in the checkpointer.
        history = trim_messages(state["messages"], max_tokens=12, token_counter=len, strategy="last",
                                start_on="human", include_system=False)
        reply = bound.invoke([SystemMessage(system)] + history)
        return {"messages": [reply], "steps": state.get("steps", 0) + 1}

    def refuse(state: ChatState):
        last = state["messages"][-1]
        answers = [ToolMessage("Отклонено: инструмент не разрешён или превышен лимит шагов.", tool_call_id=c["id"])
                   for c in last.tool_calls]
        return {"messages": answers + [AIMessage("Не могу выполнить этот шаг: он вне разрешённых действий "
                                                 "или превышен лимит шагов. Уточните вопрос.")]}

    graph = StateGraph(ChatState)
    graph.add_node("call-model", agent)
    graph.add_node("run-tools", ToolNode(tools))
    graph.add_node("refuse-tool-call", refuse)
    graph.add_edge(START, "call-model")
    graph.add_conditional_edges("call-model", RunnableLambda(route, name="route-next-step"),
                                ["run-tools", "refuse-tool-call", END])
    graph.add_edge("run-tools", "call-model")
    graph.add_edge("refuse-tool-call", END)
    return graph.compile(checkpointer=CHECKPOINTER)


def ask(service, run_id: str, question: str, thread_id: str, user_id: str = "local",
        model=None, critic=None, use_critic: bool = True) -> dict:
    app = build_agent(service, run_id, user_id, model)
    thread = f"{user_id}:{run_id}:{thread_id}"
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 4 * MAX_AGENT_STEPS}
    with traced_turn(user_id, thread, run_id, question) as trace:
        before = len(app.get_state(config).values.get("messages", []))
        state = app.invoke({"messages": [HumanMessage(question)], "steps": 0},
                           {**config, "callbacks": trace.callbacks, "run_name": "run-procurement-agent"})
        turn = state["messages"][before:]
        answer = turn[-1].content if turn else ""
        outputs = [m.content for m in turn if isinstance(m, ToolMessage)]
        used = [c["name"] for m in turn if isinstance(m, AIMessage) for c in (m.tool_calls or [])]
        with trace.step("verify-numbers", "guardrail", input=answer) as step:
            # Known facts: every tool result and tool-call argument in the whole thread (the agent may
            # answer a follow-up from an earlier turn), the question itself and the run parameters.
            thread_facts = [m.content for m in state["messages"] if isinstance(m, ToolMessage)]
            thread_facts += [json.dumps(c["args"], ensure_ascii=False) for m in state["messages"]
                             if isinstance(m, AIMessage) for c in (m.tool_calls or [])]
            params = json.dumps(service.get_result(run_id).params.model_dump(mode="json"), ensure_ascii=False)
            unsupported = check_numbers(answer, thread_facts + [question, params])
            step.update(output={"numbers_ok": not unsupported, "unsupported_numbers": unsupported})
        if use_critic:
            with trace.step("review-answer", "evaluator", input={"question": question, "answer": answer}) as step:
                verdict = review(question, outputs, answer, critic)
                step.update(output=verdict)
        else:
            verdict = {"status": "skipped", "issues": []}
        reply = {"answer": answer, "tools_used": used, "unsupported_numbers": unsupported,
                 "numbers_ok": not unsupported, "critic": verdict, "trace_id": trace.trace_id}
        trace.finish(reply)
    return reply


class Brief(BaseModel):
    supplier: str
    headline: str
    top_risks: list[str] = Field(default_factory=list, max_length=3)
    notes: list[str] = Field(default_factory=list, max_length=3)


FLAG_MEANINGS = {
    "STOCK_ESTIMATED": "остаток оценочный: приходы текущего месяца в выгрузке не видны, сверить с 1С",
    "MOQ_MISSING": "кратности нет в справочнике — заказ округлён до 1, кратность стоит уточнить",
    "MOQ_ROUNDED": "количество округлено вверх до кратности поставщика",
    "MOQ_OVERSHOOT": "округление до кратности добавило больше 50% к потребности",
    "ONE_OFF_INVOICE": "разовая крупная продажа исключена из регулярного спроса",
    "STAT_SPIKE": "аномальный месяц исключён из регулярного спроса",
    "STOCKOUT_RESTORED": "досчитан спрос за месяцы, когда товара не было на складе",
    "SEASON_PEAK": "в горизонте заказа сезонный пик",
    "TREND_UP": "устойчивый рост спроса", "TREND_DOWN": "устойчивый спад спроса",
    "NO_DEMAND": "за 12 месяцев продаж нет — не заказываем",
    "SHORT_HISTORY": "короткая история продаж — прогноз менее надёжен",
    "LEAD_ASSUMED": "срок поставки — допущение, дат заказов нет",
    "SANITY_HIGH": "заказ заметно больше обычных продаж — проверить вручную",
    "CATEGORY_NO_AUTO": "новинка или выведенный товар — решает менеджер",
    "MANAGER_RULE": "применено правило менеджера", "MANAGER_EDIT": "количество изменено менеджером",
}


def explain_run(service, run_id: str, supplier: str, model=None) -> dict:
    """Explainer: short structured summary per supplier from aggregates only (not all rows)."""
    model = model or chat_model()
    if model is None:
        raise AgentUnavailable("Сводка LLM выключена: нет OPENAI_API_KEY")
    result = service.get_result(run_id)
    part = result.orders[result.orders.supplier == supplier]
    critical = part[part.urgency == "CRITICAL"]
    bare = critical[critical.in_transit_in_horizon <= 0]
    important = bare[bare.category.isin(["A", "1"])] if "category" in bare else bare.iloc[0:0]
    top = (important if len(important) else bare).nlargest(5, "recommended_qty")[
        [c for c in ["name", "recommended_qty", "unit", "cover_days", "category"] if c in part]]
    if "cover_days" in top:
        top = top.assign(cover_days=top.cover_days.round())
    flags = part.reason_codes.explode().value_counts().head(10).to_dict()
    facts = {"summary": [s for s in result.summary if s["supplier"] == supplier],
             "critical_without_transit": int(len(bare)), "important_critical_without_transit": int(len(important)),
             "flags": flags, "flag_meanings": {k: FLAG_MEANINGS[k] for k in flags if k in FLAG_MEANINGS},
             "top_critical": top.to_dict("records"), "warnings": result.warnings}
    brief = model.with_structured_output(Brief).invoke(
        [("system", EXPLAINER_PROMPT), ("user", json.dumps(facts, ensure_ascii=False, default=str))])
    return brief.model_dump()
