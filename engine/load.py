"""Загрузка и нормализация выгрузок 1С по поставщикам.

Каждая подпапка data/raw/ — один поставщик (IEK.zip → data/raw/IEK/, Systeme electric.zip →
data/raw/SE/). Файлы ищутся по началу имени, колонки — по заголовкам, поэтому разные
форматы выгрузок (у ИЭК и SE они отличаются) читаются одним кодом.

Отказоустойчивость: без файла продаж поставщик пропускается (если это единственный
поставщик — DataError с понятным текстом). Любой другой файл может отсутствовать или быть
битым — расчёт продолжается без него, а причина попадает в Dataset.warnings.

Скорость: разобранные данные кешируются в data/cache/ (ключ — имена, размеры и даты
изменения исходников), повторный запуск читает кеш вместо Excel.
"""
from dataclasses import dataclass, field
from pathlib import Path
import os
import pickle
import re

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
try:  # DATA_DIR можно задать в .env
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
RAW = Path(os.environ.get("DATA_DIR", ROOT / "data" / "raw"))
CACHE = ROOT / "data" / "cache"
CACHE_VERSION = 6  # поднять при изменении формата Dataset

SUPPLIER_NAMES = {"IEK": "ИЭК", "SE": "Systeme Electric"}
# срок поставки, если его нельзя вывести из дат заказов в пути (допущение, видно в обосновании)
LEAD_ASSUMED = {"Systeme Electric": 45}

MONTHS_RU = {"янв": 1, "февр": 2, "март": 3, "апр": 4, "май": 5, "июнь": 6,
             "июль": 7, "авг": 8, "сент": 9, "окт": 10, "нояб": 11, "дек": 12}
MONTHS_FULL = {"январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6, "июль": 7,
               "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12}
MONTHS_GEN = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
              "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}

SOURCES = {  # ключ: (шаблоны имени, что это, что будет без него)
    "sales": (["Динамика продаж*.xlsx"], "история продаж", ""),
    "monthly": (["Ежемесячные продажи*.xlsx"], "продажи 2024 года",
                "сезонность считается по короткой истории"),
    "stock": (["Ежемесячные остатки*.xlsx"], "остатки по месяцам",
              "периоды без товара не определяются"),
    "transit": (["Путь*.xlsx", "Товар в пути*.xlsx"], "товары в пути",
                "считаем, что в пути ничего нет"),
    "moq": (["MOQ*.xlsx"], "кратность отгрузки", "заказ округляется до 1"),
    "season": (["Сезонность*.xlsx"], "сезонность поставщика", "сезонность берётся по группам товаров"),
}


class DataError(Exception):
    """Данные, без которых расчёт невозможен. Текст — для менеджера."""


@dataclass
class Dataset:
    lines: pd.DataFrame      # строки накладных: date, doc, code, qty
    items: pd.DataFrame      # code, name, unit, supplier, group, group_name, category,
                             # category_source, moq, article, unit_cost
    monthly_2024: pd.DataFrame  # code, month, qty — продажи 2024 из месячного отчёта
    stock: pd.DataFrame      # code, month, stock — остаток на 1-е число
    stock_current: pd.Series  # code → остаток на дату as_of, если поставщик его дал (SE)
    transit: pd.DataFrame    # code, doc, qty, order_date, arrival_date (order_date может быть NaT)
    lead_default: dict       # поставщик → (дней, источник: "из заказов в пути" | "допущение")
    supplier_season: dict    # поставщик → pd.Series 1..12 коэффициентов
    as_of: pd.Timestamp      # дата последней продажи в выгрузке
    warnings: list[str] = field(default_factory=list)

    @property
    def company_season(self) -> pd.Series:  # совместимость со старым кодом
        return next(iter(self.supplier_season.values()), pd.Series(1.0, index=range(1, 13)))


# ---------------------------------------------------------------- помощники

def _find(raw: Path, patterns: list[str]) -> Path | None:
    files = [f for p in patterns for f in raw.glob(p) if not f.name.startswith("~$")]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def _month_col(label) -> pd.Timestamp | None:
    s = str(label).strip().lower()
    m = re.match(r"(\w+)\.?\s+(\d{4})", s)
    if not m:
        return None
    mon = MONTHS_RU.get(m.group(1)) or MONTHS_FULL.get(m.group(1))
    return pd.Timestamp(int(m.group(2)), mon, 1) if mon else None


def _num(s: pd.Series) -> pd.Series:
    """Число из ячейки 1С: пробелы-разделители, запятая как десятичная точка."""
    if s.dtype.kind in "if":
        return s.astype(float)
    return pd.to_numeric(s.astype(str).str.replace(r"[\s\xa0]", "", regex=True)
                          .str.replace(",", "."), errors="coerce")


def _col(df: pd.DataFrame, *keys: str, exact: str | None = None):
    """Колонка по подстроке заголовка (без учёта регистра)."""
    for c in df.columns:
        name = str(c).strip().lower()
        if exact is not None and name == exact.lower():
            return c
        if exact is None and any(k in name for k in keys):
            return c
    return None


def _with_header(path: Path, must_have: str, sheet=0, max_scan: int = 6) -> pd.DataFrame:
    """Читает лист, находя строку заголовка по слову (у SE заголовок во 2-й строке)."""
    raw = pd.read_excel(path, header=None, sheet_name=sheet, nrows=max_scan)
    for i in range(len(raw)):
        if raw.iloc[i].astype(str).str.contains(must_have, case=False, na=False).any():
            return pd.read_excel(path, header=i, sheet_name=sheet, dtype=object)
    raise ValueError(f"не нашёл строку заголовка со словом «{must_have}»")


def _wide_to_long(df: pd.DataFrame, value: str) -> pd.DataFrame:
    """Широкая таблица «товар × месяц» → длинная. Колонки-месяцы находим по заголовку."""
    code_col = _col(df, "код")
    if code_col is None:
        raise ValueError("нет колонки с кодом 1С")
    months = {c: _month_col(c) for c in df.columns}
    months = {c: m for c, m in months.items() if m is not None}
    if not months:
        raise ValueError("не нашёл колонок с месяцами")
    body = df[df[code_col].notna()]
    codes = body[code_col].astype(str).str.strip()
    out = [pd.DataFrame({"code": codes, "month": m, value: _num(body[c])}) for c, m in months.items()]
    long = pd.concat(out, ignore_index=True)
    return long[~long["code"].str.lower().isin(["nan", ""])]


def _names(df: pd.DataFrame) -> pd.DataFrame:
    code_col, name_col = _col(df, "код"), _col(df, exact="Номенклатура") or _col(df, "наименование")
    unit_col = _col(df, "ед")
    if code_col is None or name_col is None:
        return pd.DataFrame(columns=["name", "unit"])
    body = df[df[code_col].notna() & df[name_col].notna()]
    out = pd.DataFrame({"name": body[name_col].astype(str).str.strip().values,
                        "unit": body[unit_col].values if unit_col is not None else None},
                       index=body[code_col].astype(str).str.strip().values)
    return out[~out.index.duplicated()]


# ---------------------------------------------------------------- читатели файлов

def _read_sales(path: Path):
    s = pd.read_excel(path, dtype=str)
    need = ["Дата", "Номер", "Документ", "Код", "Номенклатура", "Количество"]
    missing = [c for c in need if c not in s.columns]
    if missing:
        raise DataError(f"В файле «{path.name}» нет колонок: {', '.join(missing)}")
    s = s[s["Документ"].astype(str).str.startswith("Расходная накладная")].copy()
    s["date"] = pd.to_datetime(s["Дата"], format="%d.%m.%Y %H:%M:%S", errors="coerce")
    bad = s["date"].isna()
    s.loc[bad, "date"] = pd.to_datetime(s.loc[bad, "Дата"], dayfirst=True, errors="coerce")
    s["qty"] = _num(s["Количество"])
    s["code"] = s["Код"].astype(str).str.strip()
    dropped = int((s["date"].isna() | s["qty"].isna()).sum())
    s = s[s["date"].notna() & (s["qty"] > 0) & (s["date"] >= "2025-01-01")]
    if s.empty:
        raise DataError(f"В файле «{path.name}» нет ни одной продажи с 2025 года")
    # номер накладной сбрасывается каждый год → в ключ добавляем год
    doc = s["Номер"].astype(str) + "/" + s["date"].dt.year.astype(str)
    lines = pd.DataFrame({"date": s["date"].dt.normalize(), "doc": doc,
                          "code": s["code"], "qty": s["qty"]}).reset_index(drop=True)
    unit = s["Ед."] if "Ед." in s.columns else pd.Series("шт", index=s.index)
    names = pd.DataFrame({"code": s["code"], "name": s["Номенклатура"].str.strip(), "unit": unit}) \
        .groupby("code").last()
    return lines, names, dropped


def _read_monthly(path: Path):
    df = _with_header(path, "Номенклатура")
    long = _wide_to_long(df, "qty")
    return long[long["month"].dt.year == 2024].fillna({"qty": 0}), _names(df)


def _read_stock(path: Path):
    df = _with_header(path, "Номенклатура")
    return _wide_to_long(df, "stock").fillna({"stock": 0}), _names(df)


def _read_transit(path: Path, as_of_year: int):
    """Два формата: ИЭК — колонки-заказы «№ от <дата> (поступление до <дата>)»;
    SE — модель менеджера (лист TDSheet): категории, себестоимость, остатки, «в пути <дата>»."""
    sheets = pd.ExcelFile(path).sheet_names
    extra = {}
    if "TDSheet" in sheets:
        df = _with_header(path, "Код 1с", sheet="TDSheet")
        code_col = _col(df, "код 1с")
        df = df[df[code_col].notna()].copy()
        df["code"] = df[code_col].astype(str).str.strip()
        df = df.drop_duplicates("code").set_index("code")
        rows = []
        for c in df.columns:
            m = re.search(r"в пути\s+(\d{2})\.(\d{2})", str(c), re.I)
            if m:
                q = _num(df[c])
                q = q[q > 0]
                rows.append(pd.DataFrame({"code": q.index, "doc": str(c).strip(), "qty": q.values,
                                          "order_date": pd.NaT,
                                          "arrival_date": pd.Timestamp(as_of_year, int(m.group(2)),
                                                                       int(m.group(1)))}))
        transit = pd.concat(rows, ignore_index=True) if rows else _empty_transit()
        cat_col, cost_col, art_col = _col(df, "категория"), _col(df, "сс реал", "себестоим"), _col(df, "артикул")
        loc_cols = [c for c in [_col(df, exact="Свободный остаток"), _col(df, exact="Витрина"),
                                _col(df, exact="Остаток ТЗ"), _col(df, exact="Розничный склад")] if c is not None]
        if cat_col is not None:
            extra["category"] = df[cat_col].map(lambda v: str(int(float(v))) if pd.notna(v) else None)
        if cost_col is not None:
            extra["unit_cost"] = _num(df[cost_col])
        if loc_cols:  # как в модели менеджера: свободный + витрина + ТЗ + розница
            extra["stock_current"] = sum(_num(df[c]).fillna(0) for c in loc_cols)
        if art_col is not None:
            extra["article"] = df[art_col]
        return transit, extra, []

    p = pd.read_excel(path, dtype={"Код 1с": str})
    code_col = _col(p, "код")
    art_col = _col(p, "артикул")
    rows, skipped = [], []
    for col in p.columns:
        label = str(col).replace("\xa0", " ")
        od = re.search(r"от (\d+) (\w+) (\d{4})", label)
        ad = re.search(r"поступление до (\d{2}\.\d{2}\.\d{4})", label)
        if not od and not ad:
            continue  # служебная колонка
        if not (od and ad and od.group(2) in MONTHS_GEN):
            skipped.append(label.strip())
            continue
        sub = p[p[col].notna()]
        rows.append(pd.DataFrame({
            "code": sub[code_col].astype(str).str.strip(), "doc": label.split(" от ")[0].strip(),
            "qty": _num(sub[col]),
            "order_date": pd.Timestamp(int(od.group(3)), MONTHS_GEN[od.group(2)], int(od.group(1))),
            "arrival_date": pd.to_datetime(ad.group(1), format="%d.%m.%Y")}))
    transit = pd.concat(rows, ignore_index=True).dropna(subset=["qty"]) if rows else _empty_transit()
    if art_col is not None:
        a = p.set_index(p[code_col].astype(str).str.strip())[art_col].dropna()
        extra["article"] = a[~a.index.duplicated()]
    return transit, extra, skipped


def _read_moq(path: Path):
    df = _with_header(path, "Код")
    code_col = _col(df, "код")
    moq_col = _col(df, "мин", "кратн", "moq")
    if moq_col is None:
        raise ValueError("нет колонки с кратностью/мин. партией")
    df = df.dropna(subset=[code_col]).assign(code=lambda x: x[code_col].astype(str).str.strip())
    df = df.drop_duplicates("code").set_index("code")
    art_col = _col(df, "артикул")
    return _num(df[moq_col]), (df[art_col] if art_col is not None else pd.Series(dtype=str))


def _read_season(path: Path, as_of: pd.Timestamp) -> pd.Series:
    """Коэффициенты по годам из блока «Сезонность по годам»; неполный текущий месяц
    (в выгрузке до as_of) досчитываем пропорционально дням, как советует спека."""
    se = pd.read_excel(path, header=None)
    hit = [(r, c) for r in range(len(se)) for c in range(se.shape[1])
           if str(se.iat[r, c]).strip() == "Месяц"]
    if not hit:
        raise ValueError("не нашёл таблицу «Месяц / Коэф. сезонности»")
    r, c0 = hit[0]
    header = se.iloc[r].map(lambda v: "" if pd.isna(v) else str(v))
    block = se.iloc[r + 1:r + 13]
    per_year = []
    for c, h in header.items():
        m = re.search(r"Коэф\. сезонности (\d{4})", h)
        if not m:
            continue
        year = int(m.group(1))
        v = _num(block[c]).to_numpy(dtype=float)
        sales_col = header.index[header.str.contains(f"Продажи {year}")]
        if len(sales_col):  # пересчитываем из продаж, чтобы поправить неполный месяц
            sales = _num(block[sales_col[0]]).to_numpy(dtype=float, copy=True)
            if year == as_of.year:
                k = as_of.month - 1
                sales[k] = sales[k] * as_of.days_in_month / as_of.day
                sales[as_of.month:] = np.nan
            mean = np.nanmean(np.where(sales > 0, sales, np.nan))
            v = sales / mean
        v = np.where(v > 0, v, np.nan)
        per_year.append(v)
    if not per_year:
        col = header.index[header.str.upper() == "СЕЗОННОСТЬ"]
        if not len(col):
            raise ValueError("нет коэффициентов сезонности")
        per_year = [_num(block[col[0]]).to_numpy(dtype=float)]
    vals = np.nanmean(np.vstack(per_year), axis=0)
    if np.isnan(vals).any() or (vals <= 0).any():
        raise ValueError("коэффициенты сезонности неполные")
    return pd.Series(vals / vals.mean(), index=range(1, 13))


def _empty_transit() -> pd.DataFrame:
    return pd.DataFrame({"code": pd.Series(dtype=str), "doc": pd.Series(dtype=str),
                         "qty": pd.Series(dtype=float), "order_date": pd.Series(dtype="datetime64[ns]"),
                         "arrival_date": pd.Series(dtype="datetime64[ns]")})


def _abc(lines: pd.DataFrame, codes: pd.Index, as_of: pd.Timestamp) -> pd.Series:
    """ABC по числу строк накладных за 12 мес. (80/15/5%) — не зависит от единиц шт/м/упак."""
    recent = lines[lines["date"] > as_of - pd.DateOffset(months=12)]
    n = recent.groupby("code").size().reindex(codes).fillna(0).sort_values(ascending=False)
    share = n.cumsum() / max(n.sum(), 1)
    cat = pd.Series(np.where(share <= 0.80, "A", np.where(share <= 0.95, "B", "C")), index=n.index)
    cat[n == 0] = "C"
    return cat.reindex(codes)


# ---------------------------------------------------------------- поставщик целиком

# вид файла по заголовкам (распознавание Алдияра, engine/loader.py) → ключ SOURCES
DISCOVER_KINDS = {"sales_lines": "sales", "sales_monthly": "monthly", "stock_monthly": "stock",
                  "transit": "transit", "moq": "moq", "seasonality": "season"}


def _discover(raw: Path, warnings: list[str], supplier: str) -> dict[str, Path]:
    """Файлы, не найденные по имени, ищем по заголовкам — имена в архиве бывают искажены."""
    from engine.loader import discover  # openpyxl read-only, читает только первые строки листов
    try:
        found = discover(raw)
    except Exception as e:
        warnings.append(f"{supplier}: не удалось распознать файлы по заголовкам: {e}")
        return {}
    return {DISCOVER_KINDS[k]: v[0] for k, v in found.items() if k in DISCOVER_KINDS}


def _parse_supplier(raw: Path, supplier: str, warnings: list[str]):
    paths = {k: _find(raw, pats) for k, (pats, _, _) in SOURCES.items()}
    if any(v is None for v in paths.values()):
        for k, path in _discover(raw, warnings, supplier).items():
            paths[k] = paths[k] or path

    def optional(key, reader, default, *args):
        pats, what, fallback = SOURCES[key]
        if paths[key] is None:
            warnings.append(f"{supplier}: нет файла «{pats[0]}» ({what}) — {fallback}.")
            return default
        try:
            return reader(paths[key], *args)
        except Exception as e:  # битый/чужой формат — работаем дальше без этого источника
            warnings.append(f"{supplier}: не удалось прочитать «{paths[key].name}» ({what}) — "
                            f"{fallback}. Причина: {e}")
            return default

    if paths["sales"] is None:
        raise DataError(f"В {raw} нет файла «{SOURCES['sales'][0][0]}» — без истории продаж считать нечего.")
    lines, sale_names, dropped = _read_sales(paths["sales"])
    as_of = lines["date"].max()
    if dropped:
        warnings.append(f"{supplier}: пропущено строк продаж без даты или количества: {dropped}.")

    empty_names = pd.DataFrame(columns=["name", "unit"])
    monthly, ms_names = optional("monthly", _read_monthly,
                                 (pd.DataFrame(columns=["code", "month", "qty"]), empty_names))
    stock, st_names = optional("stock", _read_stock,
                               (pd.DataFrame(columns=["code", "month", "stock"]), empty_names))
    transit, extra, skipped = optional("transit", _read_transit, (_empty_transit(), {}, []), as_of.year)
    if skipped:
        warnings.append(f"{supplier}: в файле «в пути» не разобраны колонки: {'; '.join(skipped)}")
    moq_map, moq_articles = optional("moq", _read_moq, (pd.Series(dtype=float), pd.Series(dtype=str)))
    season = optional("season", _read_season, None, as_of)

    codes = sorted(set(lines["code"]) | set(stock["code"]) | set(monthly["code"]))
    items = pd.DataFrame(index=pd.Index(codes, name="code"))
    items["name"] = (sale_names["name"].combine_first(ms_names["name"])
                     .combine_first(st_names["name"]).reindex(items.index))
    items["unit"] = (sale_names["unit"].combine_first(st_names["unit"]).combine_first(ms_names["unit"])
                     .reindex(items.index).fillna("шт"))
    items = items[items["name"].notna()].copy()
    items["name"] = items["name"].astype(str).str.strip()
    items["supplier"] = supplier
    items["moq_known"] = items.index.isin(moq_map.dropna().index)
    items["moq"] = moq_map.reindex(items.index).fillna(1).clip(lower=1)
    art = extra.get("article", pd.Series(dtype=str))
    items["article"] = art.combine_first(moq_articles).reindex(items.index)
    items["unit_cost"] = extra.get("unit_cost", pd.Series(dtype=float)).reindex(items.index)
    if "category" in extra:
        cat = extra["category"].reindex(items.index)
        items["category_source"] = np.where(cat.notna(), "file", "abc")
        items["category"] = cat.fillna(_abc(lines, items.index, as_of))
    else:
        items["category"] = _abc(lines, items.index, as_of)
        items["category_source"] = "abc"

    # срок поставки: из дат заказов в пути (медиана, взвешенная по количеству), иначе допущение
    t = transit.dropna(subset=["order_date"])
    if len(t):
        days = (t["arrival_date"] - t["order_date"]).dt.days.to_numpy()
        order = np.argsort(days)
        cum = np.cumsum(t["qty"].to_numpy()[order])
        lead = (int(days[order][np.searchsorted(cum, cum[-1] / 2)]), "из дат заказов в пути")
    elif supplier in LEAD_ASSUMED:
        lead = (LEAD_ASSUMED[supplier], "допущение")
    else:
        lead = (None, "допущение")

    stock_current = extra.get("stock_current", pd.Series(dtype=float))
    stock_current = stock_current[stock_current.index.isin(items.index)]
    return dict(lines=lines, items=items.reset_index(), monthly=monthly, stock=stock,
                stock_current=stock_current, transit=transit, lead=lead, season=season, as_of=as_of)


def _has_sales(d: Path) -> bool:
    if _find(d, SOURCES["sales"][0]):
        return True
    return any(f.suffix == ".xlsx" for f in d.iterdir()) and "sales" in _discover(d, [], d.name)


def _supplier_dirs(raw: Path) -> list[tuple[Path, str]]:
    subdirs = sorted(d for d in raw.iterdir() if d.is_dir())
    with_sales = [d for d in subdirs if _has_sales(d)]
    if with_sales:
        return [(d, SUPPLIER_NAMES.get(d.name, d.name)) for d in with_sales]
    return [(raw, SUPPLIER_NAMES.get(raw.name, raw.name))]  # одна папка поставщика напрямую


def _parse(raw: Path) -> Dataset:
    if not raw.exists():
        raise DataError(f"Нет папки с данными {raw}. Распакуйте архивы поставщиков в data/raw/<поставщик>/ "
                        "или укажите путь в переменной DATA_DIR.")
    warnings: list[str] = []
    parts = []
    for d, supplier in _supplier_dirs(raw):
        try:
            parts.append(_parse_supplier(d, supplier, warnings))
        except DataError as e:
            warnings.append(f"{supplier} пропущен: {e}")
    if not parts:
        raise DataError(warnings[-1].split("пропущен: ", 1)[-1] if warnings else
                        f"В {raw} не найдено ни одного файла продаж.")

    items = pd.concat([p["items"] for p in parts], ignore_index=True)
    items = items.drop_duplicates("code")  # коды разных поставщиков не пересекаются; страховка
    items["group"] = items["code"].str[:4]
    first_word = items["name"].str.split().str[0].str.strip('"«»,.')
    items["group_name"] = items["group"].map(
        first_word.groupby(items["group"]).agg(lambda x: x.value_counts().index[0]))
    flat = pd.Series(1.0, index=range(1, 13))
    return Dataset(
        lines=pd.concat([p["lines"] for p in parts], ignore_index=True),
        items=items,
        monthly_2024=pd.concat([p["monthly"] for p in parts], ignore_index=True),
        stock=pd.concat([p["stock"] for p in parts], ignore_index=True),
        stock_current=pd.concat([p["stock_current"] for p in parts]),
        transit=pd.concat([p["transit"] for p in parts], ignore_index=True),
        lead_default={p["items"]["supplier"].iat[0]: p["lead"] for p in parts if len(p["items"])},
        supplier_season={p["items"]["supplier"].iat[0]: (p["season"] if p["season"] is not None else flat)
                         for p in parts if len(p["items"])},
        as_of=max(p["as_of"] for p in parts),
        warnings=warnings)


def load(raw: Path = RAW, use_cache: bool = True) -> Dataset:
    raw = Path(raw)
    key = (CACHE_VERSION, str(raw), tuple(sorted((str(f.relative_to(raw)), f.stat().st_size, f.stat().st_mtime_ns)
                                                 for f in raw.rglob("*.xlsx")))) if raw.exists() else None
    cache_file = CACHE / "dataset.pkl"
    if use_cache and key and cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                cached_key, ds = pickle.load(f)
            if cached_key == key:
                return ds
        except Exception:
            pass  # битый кеш — просто перечитываем Excel
    ds = _parse(raw)
    if use_cache and key:
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                pickle.dump((key, ds), f)
            tmp.replace(cache_file)  # атомарно: оборванная запись не испортит кеш
        except OSError:
            pass  # нет прав на запись — работаем без кеша
    return ds
