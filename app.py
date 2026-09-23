"""Драфт интерфейса. Владелец дальше — поток B (см. docs/PLAN.md)."""
import io

import altair as alt
import pandas as pd
import streamlit as st

from engine.calc import Params, compute
from engine.load import DataError, load

st.set_page_config(page_title="Заказы поставщикам", layout="wide")


@st.cache_resource(show_spinner="Загружаю выгрузки 1С…")
def get_data():
    return load()


@st.cache_data(show_spinner="Считаю потребность…")
def run(review_days, lead_days, service_level, growth_pct, oneoff_k, groups):
    p = Params(review_days=review_days, lead_days=lead_days, service_level=service_level,
               growth_pct=growth_pct, oneoff_k=oneoff_k, groups=list(groups) or None)
    return compute(get_data(), p)


try:
    ds = get_data()
except DataError as e:
    st.error(f"Не получилось загрузить данные. {e}")
    st.stop()
groups = ds.items.groupby("group")["group_name"].first()

DEFAULTS = {"sel": [], "review": 30, "lead": 30, "service": 0.95, "growth": 0.0, "k": 6.0}
if "params" not in st.session_state:
    st.session_state.params = DEFAULTS.copy()

with st.sidebar:
    st.header("Параметры расчёта")
    # форма: изменения применяются только по кнопке «Сохранить», а не на каждый клик
    with st.form("params_form"):
        cur = st.session_state.params
        sel = st.multiselect("Категории", groups.index, default=cur["sel"],
                             format_func=lambda g: f"{g} · {groups[g]}")
        review = st.slider("Период до следующего заказа, дн.", 7, 90, cur["review"])
        lead = st.slider("Срок поставки по умолчанию, дн.", 5, 90, cur["lead"],
                         help="Для товаров из «Пути» срок берётся из дат заказа и прихода")
        service = st.select_slider("Уровень сервиса", [0.8, 0.85, 0.9, 0.95, 0.98, 0.99], cur["service"])
        growth = st.number_input("Прогноз прироста, %", -50.0, 100.0, cur["growth"], 5.0)
        k = st.slider("Строгость отсечения разовых заказов", 3.0, 12.0, cur["k"], 0.5,
                      help="Меньше — строже: больше строк считаются разовыми")
        if st.form_submit_button("Сохранить", type="primary", width="stretch"):
            st.session_state.params = {"sel": sel, "review": review, "lead": lead,
                                       "service": service, "growth": growth, "k": k}
            st.rerun()
    if st.session_state.params != DEFAULTS and st.button("Сбросить", width="stretch"):
        st.session_state.params = DEFAULTS.copy()
        st.rerun()

p = st.session_state.params
res = run(p["review"], p["lead"], p["service"], p["growth"], p["k"], tuple(p["sel"]))
o = res.orders

st.title("Рекомендованные заказы поставщикам")
st.caption(f"Данные на {ds.as_of:%d.%m.%Y} · склад Алматы")
if res.warnings:
    with st.expander(f"⚠️ Расчёт сделан с оговорками ({len(res.warnings)})"):
        for w in res.warnings:
            st.write("• " + w)

need = o[o["order_qty"] > 0]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Позиций к заказу", len(need))
c2.metric("Критично", int((need["urgency"] == "критично").sum()))
c3.metric("Исключено разовых строк", len(res.oneoffs))
c4.metric("Товаров с досчитанным спросом", int((o["lost_qty"] > 0).sum()))

show_all = st.toggle("Показать и те, что заказывать не нужно")
view = o if show_all else need
cols = ["urgency", "code", "article", "name", "base_month", "stock_now", "in_transit",
        "forecast_need", "safety_stock", "moq", "order_qty", "reason"]
labels = {"urgency": "Срочность", "code": "Код 1С", "article": "Артикул", "name": "Наименование",
          "base_month": "Спрос/мес", "stock_now": "Остаток", "in_transit": "В пути",
          "forecast_need": "Прогноз", "safety_stock": "Страх. запас", "moq": "Кратн.",
          "order_qty": "К заказу", "reason": "Обоснование"}

for supplier, part in view.groupby("supplier"):
    st.subheader(f"Поставщик: {supplier} · {int((part['order_qty'] > 0).sum())} позиций")
    edited = st.data_editor(
        part[cols].rename(columns=labels), hide_index=True, width="stretch",
        disabled=[v for c, v in labels.items() if c != "order_qty"], key=f"ed_{supplier}",
        column_config={"Обоснование": st.column_config.TextColumn(width="large")})
    buf = io.BytesIO()
    export = edited[edited["К заказу"] > 0][["Код 1С", "Артикул", "Наименование", "К заказу"]]
    export.to_excel(buf, index=False, sheet_name="Заказ")
    st.download_button(f"Утвердить и скачать заказ {supplier} (XLSX)", buf.getvalue(),
                       file_name=f"заказ_{supplier}_{ds.as_of:%Y%m%d}.xlsx")

st.divider()
st.subheader("Почему такое количество")
code = st.selectbox("Товар", view["code"], format_func=lambda c: f"{c} · {o.set_index('code').at[c, 'name']}")
if code:
    row = o.set_index("code").loc[code]
    st.info(row["reason"])
    h = res.history[res.history["code"] == code].copy()
    h["регулярный спрос"] = h["clean_qty"] - h["lost_qty"]
    long = h.melt(id_vars="month", value_vars=["регулярный спрос", "oneoff_qty", "lost_qty", "forecast"],
                  var_name="ряд", value_name="кол-во").dropna()
    long["ряд"] = long["ряд"].replace({"oneoff_qty": "разовые (исключены)",
                                       "lost_qty": "упущенный (досчитан)", "forecast": "прогноз"})
    chart = alt.Chart(long).mark_bar().encode(
        x=alt.X("yearmonth(month):T", title=None), y=alt.Y("sum(кол-во):Q", title=row["unit"]),
        color=alt.Color("ряд:N", scale=alt.Scale(
            domain=["регулярный спрос", "упущенный (досчитан)", "разовые (исключены)", "прогноз"],
            range=["#4c78a8", "#f58518", "#bab0ac", "#54a24b"])),
        tooltip=["yearmonth(month):T", "ряд", "кол-во"])
    st.altair_chart(chart, width="stretch")
