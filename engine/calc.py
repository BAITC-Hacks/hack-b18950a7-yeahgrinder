"""Расчёт рекомендованных заказов. Методология — в README.

Порядок для каждого товара (помесячно):
  1. разовые крупные строки накладных → исключаются из регулярного спроса;
  2. месяцы без остатка на начало → спрос досчитывается до ожидаемого;
  3. прогноз = уровень без сезонности × тренд × сезонный коэффициент × прирост;
  4. заказ = спрос на (срок поставки + период) + страховой запас − остаток − в пути,
     округлённый вверх до кратности.
"""
from dataclasses import dataclass, field
from math import ceil, sqrt
from statistics import NormalDist

import numpy as np
import pandas as pd

from engine.load import Dataset

MONTH_NAMES = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


@dataclass
class Params:
    review_days: int = 30        # период до следующего заказа
    lead_days: int = 30          # срок поставки, если по товару он неизвестен
    service_level: float = 0.95  # вероятность не уйти в дефицит за горизонт
    growth_pct: float = 0.0      # прогноз прироста спроса, %
    oneoff_k: float = 6.0        # строгость отсечения разовых заказов (в MAD)
    groups: list[str] | None = None  # фильтр категорий (префиксы кода)
    transit_override: dict[str, float] = field(default_factory=dict)  # code → qty в пути (для проверок)


@dataclass
class Result:
    orders: pd.DataFrame
    history: pd.DataFrame
    oneoffs: pd.DataFrame
    warnings: list[str] = field(default_factory=list)  # что не учтено и почему — показать менеджеру
    errors: dict[str, str] = field(default_factory=dict)  # code → ошибка расчёта по товару


def _fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") if abs(x) >= 10 else f"{x:.1f}".rstrip("0").rstrip(".")


def detect_oneoffs(lines: pd.DataFrame, k: float) -> pd.DataFrame:
    """Разовая строка — намного больше обычной строки товара и сама делает месяц.

    Порог: max(медиана + k·MAD, 5·медиана). Дополнительно строка должна давать ≥50%
    продаж товара за свой месяц — иначе это просто оживлённый месяц, а не разовая сделка.
    """
    g = lines.groupby("code")["qty"]
    med = g.transform("median")
    mad = (lines["qty"] - med).abs().groupby(lines["code"]).transform("median") * 1.4826
    n = g.transform("size")
    thr = np.maximum(med + k * mad, 5 * med)
    month = lines["date"].dt.to_period("M")
    month_total = lines.groupby([lines["code"], month])["qty"].transform("sum")
    frequent = (n >= 5) & (lines["qty"] > thr) & (lines["qty"] >= 0.5 * month_total)
    # редкий товар (< 5 строк): разовая — строка, которая одна даёт ≥ 80% всех продаж
    # товара и в 10+ раз больше остальных его строк
    total = g.transform("sum")
    others_med = ((total - lines["qty"]) / (n - 1).clip(lower=1)).clip(lower=1)
    rare = (n < 5) & (lines["qty"] >= 0.8 * total) & (lines["qty"] >= 10 * others_med)
    flag = frequent | rare
    thr = thr.where(frequent, 10 * others_med)
    med = med.where(frequent, others_med)
    out = lines[flag].copy()
    out["threshold"] = thr[flag]
    out["typical"] = med[flag]
    return out


def _season_index(series: np.ndarray, months: pd.DatetimeIndex) -> np.ndarray | None:
    """Коэффициенты по 12 месяцам из полных лет ряда (среднее отношений к среднему года)."""
    ratios = []
    for year in sorted(set(months.year)):
        mask = months.year == year
        if mask.sum() < 12:
            continue
        y = series[mask]
        if y.mean() <= 0:
            continue
        ratios.append(y / y.mean())
    if not ratios:
        return None
    idx = np.mean(ratios, axis=0)
    return idx / idx.mean()


def _sanitize(p: Params) -> Params:
    """Параметры из UI/агента приводим к допустимым границам, а не падаем."""
    return Params(review_days=int(min(max(p.review_days, 1), 365)),
                  lead_days=int(min(max(p.lead_days, 1), 365)),
                  service_level=float(min(max(p.service_level, 0.5), 0.999)),
                  growth_pct=float(min(max(p.growth_pct, -90), 500)),
                  oneoff_k=float(min(max(p.oneoff_k, 1), 50)),
                  groups=p.groups, transit_override=dict(p.transit_override))


def compute(ds: Dataset, p: Params = Params()) -> Result:
    p = _sanitize(p)
    as_of = ds.as_of
    cur_month = as_of.to_period("M").to_timestamp()
    months = pd.date_range("2024-01-01", cur_month, freq="MS")  # последний — неполный
    hist = months[:-1]
    H = len(hist)

    items = ds.items.set_index("code")
    if p.groups:
        items = items[items["group"].isin(p.groups)]
    codes = items.index

    # --- помесячные продажи: 2024 из месячного отчёта, 2025+ из накладных
    lines = ds.lines[ds.lines["code"].isin(codes)]
    oneoffs = detect_oneoffs(lines, p.oneoff_k)
    lines = lines.assign(month=lines["date"].dt.to_period("M").dt.to_timestamp())
    raw = lines.pivot_table(index="code", columns="month", values="qty", aggfunc="sum")
    m24 = ds.monthly_2024[ds.monthly_2024["code"].isin(codes)]
    raw = raw.combine_first(m24.pivot_table(index="code", columns="month", values="qty", aggfunc="sum"))
    raw = raw.reindex(index=codes, columns=months).fillna(0.0)

    oo = oneoffs.assign(month=oneoffs["date"].dt.to_period("M").dt.to_timestamp(),
                        excess=oneoffs["qty"] - oneoffs["typical"])
    oneoff_m = (oo.pivot_table(index="code", columns="month", values="excess", aggfunc="sum")
                  .reindex(index=codes, columns=months).fillna(0.0))

    stock = (ds.stock[ds.stock["code"].isin(codes)]
             .pivot_table(index="code", columns="month", values="stock", aggfunc="sum")
             .reindex(index=codes, columns=months).fillna(0.0))

    transit = ds.transit[ds.transit["code"].isin(codes)]
    lead_by_code = ((transit["arrival_date"] - transit["order_date"]).dt.days
                    .groupby(transit["code"]).median())
    comp_season = ds.company_season.reindex(range(1, 13)).to_numpy()

    # --- 2024: строк нет, выбросы ловим по месяцам (Hampel)
    raw_np, oneoff_np = raw.to_numpy(copy=True), oneoff_m.to_numpy(copy=True)
    y24 = months.year == 2024
    clean_np = raw_np - oneoff_np
    for i in range(len(codes)):
        x = clean_np[i, :H]
        nz = x[x > 0]
        if len(nz) < 6:
            continue
        med = np.median(nz)
        mad = np.median(np.abs(nz - med)) * 1.4826
        cap = max(med + p.oneoff_k * mad, 5 * med)
        bad = y24[:H] & (x > cap)
        if bad.any():
            oneoff_np[i, :H][bad] += x[bad] - med
            clean_np[i, :H][bad] = med

    # --- групповая сезонность (из очищенных продаж), с усадкой к компании
    moy = hist.month.to_numpy() - 1
    group_season = {}
    for g, idx in pd.Series(range(len(codes)), index=items["group"].to_numpy()).groupby(level=0):
        s = _season_index(clean_np[idx.to_numpy(), :H].sum(axis=0), hist)
        w = 0.7 if len(idx) >= 5 else 0.4
        group_season[g] = comp_season if s is None else w * s + (1 - w) * comp_season

    z = NormalDist().inv_cdf(p.service_level)

    # --- всё, что нужно внутри цикла, раскладываем по товарам заранее (без фильтров в цикле)
    item_rec = items.to_dict("index")
    stock_np = stock.to_numpy()
    oo_count = oo.groupby("code").size().to_dict()
    biggest = (oneoffs.loc[oneoffs.groupby("code")["qty"].idxmax()].set_index("code")
               if len(oneoffs) else pd.DataFrame(columns=["qty", "date", "typical"]))
    biggest = biggest.to_dict("index")
    transit_rec = {c: (g["qty"].to_numpy(float), g["arrival_date"].to_numpy())
                   for c, g in transit.groupby("code")}
    max_h = 365 * 2 + 1
    future = pd.date_range(as_of + pd.Timedelta(days=1), periods=max_h, freq="D")
    f_month = future.month.to_numpy()
    f_hm = (future.year.to_numpy() - as_of.year) * 12 + f_month - as_of.month + 0.5
    f_dim = future.days_in_month.to_numpy().astype(float)
    f_dates = future.to_numpy()
    fut_months = pd.date_range(cur_month, periods=6, freq="MS")
    idx_h = np.arange(H)
    y24h = y24[:H]

    rows, errors = [], {}
    hist_raw, hist_oneoff, hist_lost, hist_clean, hist_fc, hist_codes = [], [], [], [], [], []

    for i, code in enumerate(codes):
        try:
            it = item_rec[code]
            x = clean_np[i, :H].copy()
            active = np.flatnonzero(raw_np[i, :H] > 0)
            lead = int(lead_by_code.get(code, p.lead_days))
            horizon = min(lead + p.review_days, max_h)

            # сезонность: своя (если ≥ 18 мес. продаж и объём), иначе группы
            season, season_src = group_season[it["group"]], "группы"
            if len(active) >= 18 and x.mean() >= 10:
                own = _season_index(x, hist)
                if own is not None:
                    season, season_src = 0.6 * own + 0.4 * group_season[it["group"]], "товара"
            season = np.clip(np.nan_to_num(season, nan=1.0), 0.3, 3.0)
            season = season / season.mean()

            # упущенный спрос: месяцы без остатка на начало, после появления товара
            lost = np.zeros(H)
            first = active[0] if len(active) else H
            st_row = stock_np[i, :H]
            out_of_stock = (idx_h > first) & (st_row <= 0)
            in_stock = (idx_h >= first) & ~out_of_stock
            if out_of_stock.any() and in_stock.sum() >= 3:
                base_d = np.median(x[in_stock] / season[moy[in_stock]])
                lost = np.where(out_of_stock, np.maximum(base_d * season[moy] - x, 0), 0)
            x_full = x + lost

            # уровень и тренд (без сезонности)
            d = x_full / season[moy]
            d_act = d[first:] if first < H else np.array([])
            growth_m = 1.0
            if len(d_act) == 0:
                level, sigma = 0.0, 0.0
            else:
                last = d_act[-6:]
                level = float(np.average(last, weights=np.arange(1, len(last) + 1)))
                if len(d_act) >= 12:
                    prev = d_act[-12:-6]
                    if prev.mean() > 0 and (prev > 0).sum() >= 3 and (last > 0).sum() >= 3:
                        trend_ratio = float(np.clip(last.mean() / prev.mean(), 0.67, 1.5))
                        yoy_ok = len(d_act) < 24 or (np.sign(d_act[-12:].mean() - d_act[-24:-12].mean())
                                                     == np.sign(trend_ratio - 1))
                        if abs(trend_ratio - 1) >= 0.1 and yoy_ok:
                            growth_m = trend_ratio ** (1 / 6)
                level *= growth_m ** 2.5  # взвешенное среднее центрировано ~2.5 мес. назад
                sigma = float(np.std(d_act[-12:])) if len(d_act) >= 3 else level
            if not np.isfinite(level) or level < 0:
                raise ValueError(f"некорректный уровень спроса {level}")

            uplift = 1 + p.growth_pct / 100
            m_h = f_month[:horizon]
            daily = (level * np.minimum(growth_m ** f_hm[:horizon], 1.5) * season[m_h - 1] * uplift
                     / f_dim[:horizon])
            forecast_need = float(daily.sum())
            safety = z * sigma * sqrt(horizon / 30) * uplift if level > 0 else 0.0

            stock_now = max(float(np.nan_to_num(stock_np[i, H] - raw_np[i, H])), 0.0)
            if code in p.transit_override:
                in_transit = float(p.transit_override[code])
            elif code in transit_rec:
                q, arr = transit_rec[code]
                in_transit = float(q[arr <= f_dates[horizon - 1]].sum())
            else:
                in_transit = 0.0

            need = forecast_need + safety - stock_now - in_transit
            moq = float(it["moq"]) if it["moq"] and np.isfinite(it["moq"]) else 1.0
            order = ceil(need / moq - 1e-9) * moq if need > 0 and level >= 0.2 else 0.0
            daily_now = float(daily[:30].mean()) if len(daily) else 0.0
            days_cover = stock_now / daily_now if daily_now > 0 else float("inf")

            if order <= 0:
                urgency = "не нужно"
            elif days_cover < lead:
                urgency = "критично"
            elif days_cover < lead + p.review_days / 2:
                urgency = "высокая"
            else:
                urgency = "плановая"

            # обоснование
            u = it["unit"] or "шт"
            base_month = level * uplift
            parts = [f"Регулярный спрос ≈ {_fmt(base_month)} {u}/мес"]
            if growth_m != 1.0:
                parts[-1] += (f", устойчивый {'рост' if growth_m > 1 else 'спад'} "
                              f"{(growth_m ** 6 - 1) * 100:+.0f}% за полгода")
            if p.growth_pct:
                parts[-1] += f", с приростом {p.growth_pct:+.0f}%"
            coefs = ", ".join(f"{MONTH_NAMES[m - 1]} ×{season[m - 1]:.2f}" for m in sorted(set(m_h)))
            parts.append(f"Прогноз на {horizon} дн. (поставка {lead} + период {p.review_days}): "
                         f"{_fmt(forecast_need)} — сезонность {season_src}: {coefs}")
            n_oo = oo_count.get(code, 0) + int((oneoff_np[i, :H][y24h] > 0).sum())
            if n_oo:
                if code in biggest:
                    b = biggest[code]
                    parts.append(f"Исключено разовых заказов: {n_oo} (крупнейший {_fmt(b['qty'])} {u} "
                                 f"{b['date']:%d.%m.%Y}, обычная строка {_fmt(b['typical'])})")
                else:
                    parts.append(f"Исключено аномальных месяцев: {n_oo}")
            if lost.sum() > 0:
                parts.append(f"Досчитан упущенный спрос {_fmt(lost.sum())} {u} "
                             f"за {int(out_of_stock.sum())} мес. без остатка")
            parts.append(f"Страховой запас {_fmt(safety)} (сервис {p.service_level:.0%}). "
                         f"Остаток {_fmt(stock_now)}, в пути {_fmt(in_transit)}")
            if order > 0:
                parts.append(f"Нужно {_fmt(need)} → кратность {_fmt(moq)} → заказ {_fmt(order)}")
            else:
                parts.append("Запаса хватает — заказ не нужен" if level >= 0.2
                             else "Спроса почти нет — не заказываем")

            rows.append({
                "code": code, "article": it["article"], "name": it["name"], "unit": u,
                "group": it["group"], "group_name": it["group_name"], "supplier": it["supplier"],
                "base_month": round(base_month, 2), "forecast_need": round(forecast_need, 1),
                "safety_stock": round(safety, 1), "stock_now": stock_now, "in_transit": in_transit,
                "moq": moq, "order_qty": order, "days_cover": round(days_cover, 1), "lead_days": lead,
                "urgency": urgency, "reason": ". ".join(parts) + ".",
                "n_oneoff": n_oo, "lost_qty": round(float(lost.sum()), 1),
            })
            hist_codes.append(code)
            hist_raw.append(raw_np[i, :H + 1])
            hist_oneoff.append(oneoff_np[i, :H])
            hist_lost.append(lost)
            hist_clean.append(x_full)
            hist_fc.append([level * min(growth_m ** (k + 0.5), 1.5) * season[m.month - 1] * uplift
                            for k, m in enumerate(fut_months)])
        except Exception as e:  # один битый товар не должен ронять весь расчёт
            errors[code] = f"{type(e).__name__}: {e}"

    # история для графиков — одной таблицей, без DataFrame на каждый товар
    n, pad5, pad6 = len(hist_codes), np.full((len(hist_codes), 5), np.nan), np.full((len(hist_codes), 6), np.nan)
    all_months = list(hist) + list(fut_months)
    if n:
        history = pd.DataFrame({
            "code": np.repeat(hist_codes, len(all_months)),
            "month": np.tile(np.array(all_months, dtype="datetime64[ns]"), n),
            "raw_qty": np.hstack([np.array(hist_raw), pad5]).ravel(),
            "oneoff_qty": np.hstack([np.array(hist_oneoff), pad6]).ravel(),
            "lost_qty": np.hstack([np.array(hist_lost), pad6]).ravel(),
            "clean_qty": np.hstack([np.array(hist_clean), pad6]).ravel(),
            "forecast": np.hstack([np.full((n, H), np.nan), np.array(hist_fc)]).ravel(),
        })
    else:
        history = pd.DataFrame(columns=["code", "month", "raw_qty", "oneoff_qty", "lost_qty",
                                        "clean_qty", "forecast"])

    warnings = list(ds.warnings)
    if errors:
        warnings.append(f"Не удалось посчитать {len(errors)} товаров — они не попали в список")

    orders = pd.DataFrame(rows)
    rank = {"критично": 0, "высокая": 1, "плановая": 2, "не нужно": 3}
    orders = orders.sort_values(["supplier", "urgency", "order_qty"],
                                key=lambda s: s.map(rank) if s.name == "urgency" else s,
                                ascending=[True, True, False]).reset_index(drop=True)
    return Result(orders=orders, history=history,
                  oneoffs=oneoffs[["date", "doc", "code", "qty", "threshold"]].reset_index(drop=True),
                  warnings=warnings, errors=errors)
