"""Загрузка и нормализация выгрузок партнёра (IEK.zip → data/raw/IEK/).

Отказоустойчивость: без файла продаж работать нельзя — это DataError с понятным текстом.
Любой другой файл может отсутствовать или быть битым — расчёт продолжается без него,
а причина попадает в Dataset.warnings и показывается менеджеру.

Скорость: разобранные данные кешируются в data/cache/ (ключ — имена, размеры и даты
изменения исходников), повторный запуск читает кеш вместо Excel.
"""
from dataclasses import dataclass, field
from pathlib import Path
import os
import pickle
import re

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = Path(os.environ.get("DATA_DIR", ROOT / "data" / "raw" / "IEK"))
CACHE = ROOT / "data" / "cache"
CACHE_VERSION = 3  # поднять при изменении формата Dataset

MONTHS_RU = {"янв": 1, "февр": 2, "март": 3, "апр": 4, "май": 5, "июнь": 6,
             "июль": 7, "авг": 8, "сент": 9, "окт": 10, "нояб": 11, "дек": 12}
MONTHS_GEN = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
              "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}

# ищем файлы по началу имени: новая выгрузка с другой датой в названии подхватится сама
SOURCES = {  # ключ: (шаблон имени, что это, что будет без него)
    "sales": ("Динамика продаж*.xlsx", "история продаж", ""),
    "monthly": ("Ежемесячные продажи*.xlsx", "продажи 2024 года",
                "сезонность считается по короткой истории"),
    "stock": ("Ежемесячные остатки*.xlsx", "остатки",
              "текущий остаток считается нулевым, периоды без товара не определяются"),
    "transit": ("Путь*.xlsx", "товары в пути", "считаем, что в пути ничего нет"),
    "moq": ("MOQ*.xlsx", "кратность отгрузки", "заказ округляется до 1"),
    "season": ("Сезонность*.xlsx", "сезонность компании", "сезонность берётся по группам товаров"),
}


class DataError(Exception):
    """Данные, без которых расчёт невозможен. Текст — для менеджера."""


@dataclass
class Dataset:
    lines: pd.DataFrame      # строки накладных: date, doc, code, qty
    items: pd.DataFrame      # справочник: code, name, unit, group, group_name, moq, article, supplier
    monthly_2024: pd.DataFrame  # code, month, qty — продажи 2024 из месячного отчёта
    stock: pd.DataFrame      # code, month, stock — остаток на 1-е число
    transit: pd.DataFrame    # code, doc, qty, order_date, arrival_date
    company_season: pd.Series  # индекс 1..12 → коэффициент сезонности компании
    as_of: pd.Timestamp      # дата последней продажи в выгрузке
    warnings: list[str] = field(default_factory=list)


def _find(raw: Path, pattern: str) -> Path | None:
    files = [f for f in raw.glob(pattern) if not f.name.startswith("~$")]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def _month_col(label) -> pd.Timestamp | None:
    m = re.match(r"(\w+)\.?\s+(\d{4})", str(label).strip())
    if not m or m.group(1) not in MONTHS_RU:
        return None
    return pd.Timestamp(int(m.group(2)), MONTHS_RU[m.group(1)], 1)


def _num(s: pd.Series) -> pd.Series:
    """Число из ячейки 1С: пробелы-разделители, запятая как десятичная точка."""
    if s.dtype.kind in "if":
        return s.astype(float)
    return pd.to_numeric(s.astype(str).str.replace(r"[\s\xa0]", "", regex=True)
                          .str.replace(",", "."), errors="coerce")


def _wide_to_long(df: pd.DataFrame, code_col: int, value: str) -> pd.DataFrame:
    """Широкая таблица «товар × месяц» → длинная. Колонки-месяцы находим по заголовку."""
    header = df.iloc[0]
    months = {i: _month_col(header.iloc[i]) for i in range(df.shape[1])}
    months = {i: m for i, m in months.items() if m is not None}
    if not months:
        raise ValueError("не нашёл колонок с месяцами в первой строке")
    body = df.iloc[1:]
    body = body[body.iloc[:, code_col].notna()]
    codes = body.iloc[:, code_col].astype(str).str.strip()
    out = [pd.DataFrame({"code": codes, "month": m, value: _num(body.iloc[:, i])})
           for i, m in months.items()]
    long = pd.concat(out, ignore_index=True)
    return long[long["code"].str.lower() != "nan"]


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
    lines = pd.DataFrame({"date": s["date"].dt.normalize(), "doc": s["Номер"].astype(str),
                          "code": s["code"], "qty": s["qty"]}).reset_index(drop=True)
    unit = s["Ед."] if "Ед." in s.columns else pd.Series("шт", index=s.index)
    names = pd.DataFrame({"code": s["code"], "name": s["Номенклатура"], "unit": unit}) \
        .groupby("code").last()
    return lines, names, dropped


def _read_monthly(path: Path):
    ms = pd.read_excel(path, header=None)
    names = ms.iloc[1:].dropna(subset=[1]).set_index(ms.iloc[1:].dropna(subset=[1])[1].astype(str).str.strip())[0]
    long = _wide_to_long(ms, code_col=1, value="qty")
    return long[long["month"].dt.year == 2024].fillna({"qty": 0}), names.dropna().astype(str).str.strip()


def _read_stock(path: Path):
    st = pd.read_excel(path, header=None)
    body = st.iloc[1:].dropna(subset=[2])
    names = pd.DataFrame({"name": body[0].values, "unit": body[1].values},
                         index=body[2].astype(str).str.strip())
    names = names[names["name"].notna()]
    return _wide_to_long(st, code_col=2, value="stock").fillna({"stock": 0}), names


def _read_transit(path: Path):
    p = pd.read_excel(path, dtype={"Код 1с": str})
    code_col = next(c for c in p.columns if "код" in str(c).lower())
    art_col = next((c for c in p.columns if "артикул" in str(c).lower()), None)
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
    articles = pd.Series(dtype=str)
    if art_col is not None:
        articles = p.set_index(p[code_col].astype(str).str.strip())[art_col].dropna()
        articles = articles[~articles.index.duplicated()]
    return transit, articles, skipped


def _read_moq(path: Path):
    moq = pd.read_excel(path, dtype=str)
    code_col = next(c for c in moq.columns if "код" in str(c).lower())
    moq_col = next(c for c in moq.columns if "мин" in str(c).lower() or "moq" in str(c).lower())
    moq = moq.dropna(subset=[code_col]).assign(code=lambda x: x[code_col].str.strip())
    moq = moq.drop_duplicates("code").set_index("code")
    art_col = next((c for c in moq.columns if "артикул" in str(c).lower()), None)
    return _num(moq[moq_col]), (moq[art_col] if art_col else pd.Series(dtype=str))


def _read_season(path: Path) -> pd.Series:
    se = pd.read_excel(path, header=None)
    hit = se.index[se[1].astype(str).str.contains("СРЕДНЯЯ СЕЗОННОСТЬ", na=False)]
    if len(hit) == 0:
        raise ValueError("не нашёл блок «СРЕДНЯЯ СЕЗОННОСТЬ»")
    vals = _num(se.iloc[hit[0] + 2:hit[0] + 14, 5]).to_numpy()
    if len(vals) != 12 or pd.isna(vals).any() or (vals <= 0).any():
        raise ValueError("коэффициенты сезонности неполные")
    return pd.Series(vals / vals.mean(), index=range(1, 13))


def _empty_transit() -> pd.DataFrame:
    return pd.DataFrame({"code": pd.Series(dtype=str), "doc": pd.Series(dtype=str),
                         "qty": pd.Series(dtype=float), "order_date": pd.Series(dtype="datetime64[ns]"),
                         "arrival_date": pd.Series(dtype="datetime64[ns]")})


def _parse(raw: Path) -> Dataset:
    warnings: list[str] = []
    paths = {k: _find(raw, pat) for k, (pat, _, _) in SOURCES.items()}

    def optional(key, reader, default):
        pat, what, fallback = SOURCES[key]
        if paths[key] is None:
            warnings.append(f"Нет файла «{pat}» ({what}) — {fallback}.")
            return default
        try:
            return reader(paths[key])
        except Exception as e:  # битый/чужой формат — работаем дальше без этого источника
            warnings.append(f"Не удалось прочитать «{paths[key].name}» ({what}) — {fallback}. "
                            f"Причина: {e}")
            return default

    if not raw.exists():
        raise DataError(f"Нет папки с данными {raw}. Распакуйте IEK.zip в data/raw/ "
                        "или укажите путь в переменной DATA_DIR.")
    if paths["sales"] is None:
        raise DataError(f"В {raw} нет файла «{SOURCES['sales'][0]}» — без истории продаж считать нечего.")
    lines, sale_names, dropped = _read_sales(paths["sales"])
    if dropped:
        warnings.append(f"Пропущено строк продаж без даты или количества: {dropped}.")

    monthly, ms_names = optional("monthly", _read_monthly,
                                 (pd.DataFrame(columns=["code", "month", "qty"]), pd.Series(dtype=str)))
    stock, st_names = optional("stock", _read_stock,
                               (pd.DataFrame(columns=["code", "month", "stock"]),
                                pd.DataFrame(columns=["name", "unit"])))
    transit, articles, skipped = optional("transit", _read_transit, (_empty_transit(), pd.Series(dtype=str), []))
    if skipped:
        warnings.append(f"В «Пути» не разобраны колонки: {'; '.join(skipped)}")
    moq_map, moq_articles = optional("moq", _read_moq, (pd.Series(dtype=float), pd.Series(dtype=str)))
    company_season = optional("season", _read_season, None)

    codes = sorted(set(lines["code"]) | set(stock["code"]) | set(monthly["code"]))
    items = pd.DataFrame(index=pd.Index(codes, name="code"))
    items["name"] = sale_names["name"].combine_first(ms_names).combine_first(st_names["name"]) \
        .reindex(items.index)
    items["unit"] = sale_names["unit"].combine_first(st_names["unit"]).reindex(items.index).fillna("шт")
    items = items[items["name"].notna()]
    items["name"] = items["name"].astype(str).str.strip()
    items["moq"] = moq_map.reindex(items.index).fillna(1).clip(lower=1)
    items["article"] = articles.combine_first(moq_articles).reindex(items.index)
    items["group"] = items.index.str[:4]
    first_word = items["name"].str.split().str[0].str.strip('"«»,.')
    items["group_name"] = items["group"].map(
        first_word.groupby(items["group"]).agg(lambda x: x.value_counts().index[0]))
    items["supplier"] = "ИЭК"

    if company_season is None:
        company_season = pd.Series(1.0, index=range(1, 13))
    return Dataset(lines=lines, items=items.reset_index(), monthly_2024=monthly, stock=stock,
                   transit=transit, company_season=company_season, as_of=lines["date"].max(),
                   warnings=warnings)


def load(raw: Path = RAW, use_cache: bool = True) -> Dataset:
    raw = Path(raw)
    key = (CACHE_VERSION, str(raw), tuple(sorted((f.name, f.stat().st_size, f.stat().st_mtime_ns)
                                                 for f in raw.glob("*.xlsx")))) if raw.exists() else None
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
