"""Offline contract tests for frontend data loading and real XLSX generation.

Run with the project's Python environment and Node.js 18+ installed:
    python -m pytest tests/test_frontend_modules.py -q

These tests exercise ES modules without a browser, package.json changes or an API
server. They prefer the active web/ frontend, falling back to the root layout for
older checkouts. They do not replace the manual UI checks in the teammate guide.
"""

import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = ROOT / "web" if (ROOT / "web").is_dir() else ROOT
SHEET_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
TYPE_NS = {"t": "http://schemas.openxmlformats.org/package/2006/content-types"}
HEADERS = ["Код 1С", "Артикул", "Наименование", "Ед.", "Количество", "Поставщик", "Срочность"]


@pytest.fixture(scope="module")
def run_js():
    """Import a fresh module for each test and reject any unmocked network call."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Frontend module tests require Node.js 18+; install Node for the full suite")
    version = subprocess.run(
        [node, "--version"], check=True, capture_output=True, text=True, timeout=10
    ).stdout.strip()
    if int(version.lstrip("v").split(".")[0]) < 18:
        pytest.skip(f"Frontend module tests require Node.js 18+ (found {version})")

    def execute(filename, body, payload=None):
        # A data URL keeps .js sources as ES modules even without package.json.
        prelude = """
import { readFileSync } from 'node:fs';
globalThis.fetch = async () => { throw new Error('Unexpected network request in test'); };
const input = JSON.parse(process.argv[3]);
const source = readFileSync(process.argv[2], 'utf8');
const module = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
"""
        result = subprocess.run(
            [node, "--input-type=module", "-", str(FRONTEND_ROOT / filename), json.dumps(payload)],
            input=prelude + body,
            text=True,
            capture_output=True,
            timeout=20,
            cwd=ROOT,
            env={**os.environ, "TZ": "UTC"},
        )
        assert result.returncode == 0, f"Node module test failed:\n{result.stderr}\n{result.stdout}"
        return json.loads(result.stdout)

    return execute


def workbook_files(run_js, rows, meta=None):
    result = run_js(
        "export.js",
        """
const blob = module.buildWorkbook(input.rows, input.meta || {});
console.log(JSON.stringify({type: blob.type, bytes: Buffer.from(await blob.arrayBuffer()).toString('base64')}));
""",
        {"rows": rows, "meta": meta},
    )
    assert result["type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    raw = base64.b64decode(result["bytes"])
    assert raw.startswith(b"PK\x03\x04"), "Export must be an XLSX ZIP, not renamed CSV"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.testzip() is None, "Every ZIP member must have a correct CRC"
        files = {name: archive.read(name) for name in archive.namelist()}
    # Check every generated XML part, including the package relationships.
    for content in files.values():
        ET.fromstring(content)
    return files


def cell_value(cell):
    if cell.get("t") == "inlineStr":
        return "".join(cell.itertext())
    return cell.findtext("s:v", namespaces=SHEET_NS)


def sheet_rows(content):
    sheet = ET.fromstring(content)
    return [[cell_value(cell) for cell in row] for row in sheet.findall("s:sheetData/s:row", SHEET_NS)]


def test_xlsx_has_seven_columns_and_preserves_supplied_order_quantities(run_js):
    # 37.5 is the manager's final value passed by the UI, independent of a recommendation.
    rows = [
        ["0000123_", "A-01", "Товар с ручным количеством", "шт.", 37.5, "IEK", "Высокая"],
        ["0000456_", "C-305", "Кабель", "м", 610, "Systeme Electric", "Критично"],
    ]
    files = workbook_files(run_js, rows)
    assert set(files) == {
        "[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml",
    }
    exported = sheet_rows(files["xl/worksheets/sheet1.xml"])
    assert exported == [HEADERS, [*rows[0][:4], "37.5", *rows[0][5:]], [*rows[1][:4], "610", *rows[1][5:]]]
    assert all(len(row) == 7 for row in exported)
    sheet = ET.fromstring(files["xl/worksheets/sheet1.xml"])
    for ref, expected in (("E2", "37.5"), ("E3", "610")):
        cell = sheet.find(f".//s:c[@r='{ref}']", SHEET_NS)
        assert cell is not None and cell.get("t") is None
        assert cell.findtext("s:v", namespaces=SHEET_NS) == expected
    assert sheet.find("s:autoFilter", SHEET_NS).get("ref") == "A1:G3"
    assert sheet.find("s:sheetViews/s:sheetView/s:pane", SHEET_NS).get("state") == "frozen"


def test_xlsx_escapes_text_and_keeps_formula_like_names_as_text(run_js):
    raw_name = '  Кабель & <щит> "двойной" \'одинарный\'\x00\x08\x0b\x0c\x0e\x1f  '
    cleaned_name = '  Кабель & <щит> "двойной" \'одинарный\'  '
    formula_like_article = '=HYPERLINK("https://example.invalid","текст")'
    files = workbook_files(run_js, [["001_", formula_like_article, raw_name, "м", 15, "IEK", "Плановая"]])
    sheet = ET.fromstring(files["xl/worksheets/sheet1.xml"])
    assert sheet_rows(files["xl/worksheets/sheet1.xml"])[1] == [
        "001_", formula_like_article, cleaned_name, "м", "15", "IEK", "Плановая"
    ]
    article = sheet.find(".//s:c[@r='B2']", SHEET_NS)
    assert article.get("t") == "inlineStr"
    assert sheet.findall(".//s:f", SHEET_NS) == []


def test_approval_metadata_adds_linked_sheet_without_changing_order_sheet(run_js):
    rows = [["001_", "A1", "Товар", "шт.", 120, "IEK", "Критично"]]
    plain = workbook_files(run_js, rows)
    approved = workbook_files(run_js, rows, {"by": "Менеджер & команда", "at": "2026-09-23T10:30:00Z"})
    assert approved["xl/worksheets/sheet1.xml"] == plain["xl/worksheets/sheet1.xml"]
    sheets = ET.fromstring(approved["xl/workbook.xml"]).findall("s:sheets/s:sheet", SHEET_NS)
    assert [sheet.get("name") for sheet in sheets] == ["Заказ", "Утверждение"]
    relation_id = sheets[1].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    relations = ET.fromstring(approved["xl/_rels/workbook.xml.rels"])
    assert relations.find(f"r:Relationship[@Id='{relation_id}']", REL_NS).get("Target") == "worksheets/sheet2.xml"
    types = ET.fromstring(approved["[Content_Types].xml"])
    assert types.find("t:Override[@PartName='/xl/worksheets/sheet2.xml']", TYPE_NS) is not None
    metadata = dict(sheet_rows(approved["xl/worksheets/sheet2.xml"]))
    assert metadata["Утвердил"] == "Менеджер & команда"
    assert "2026" in metadata["Дата и время"] and "10:30" in metadata["Дата и время"]
    assert metadata["Позиций"] == "1"
    assert metadata["Отправка поставщику"] == "нет — файл для загрузки в 1С"
    assert "xl/worksheets/sheet2.xml" not in plain


def test_provider_uses_api_payload_and_shares_one_request(run_js):
    payload = {
        "meta": {
            "asOf": "2026-09-23", "reviewDays": 45, "leadText": "30 дней",
            "orderValue": 12000, "warnings": ["Тестовый источник"],
        },
        "products": [{"id": "api-only", "sku": "001_", "recommended_qty": 50, "unit": "шт."}],
        "sources": [{"name": "Тестовый API", "status": "Загружено"}],
    }
    result = run_js(
        "mockDashboard.js",
        """
const requests = [];
globalThis.fetch = async (url, options) => {
  requests.push({url, options});
  return {ok: true, json: async () => input};
};
const [products, sources] = await Promise.all([module.dataProvider.getProducts(), module.dataProvider.getSources()]);
const again = await module.dataProvider.getProducts();
console.log(JSON.stringify({products, sources, again, demo: module.demo, requests}));
""",
        payload,
    )
    assert result["products"] == payload["products"] == result["again"]
    assert result["sources"] == payload["sources"]
    assert result["requests"] == [{"url": "./ui/data", "options": {"headers": {"Accept": "application/json"}}}]
    assert result["demo"]["live"] is True
    for key, value in payload["meta"].items():
        assert result["demo"][key] == value


@pytest.mark.parametrize("failure,error", [("network", "offline test"), ("http", "HTTP 503"), ("json", "invalid JSON test")])
def test_provider_falls_back_offline_for_api_failures(run_js, failure, error):
    result = run_js(
        "mockDashboard.js",
        """
let calls = 0;
globalThis.fetch = async () => {
  calls += 1;
  if (input === 'network') throw new Error('offline test');
  if (input === 'http') return {ok: false, status: 503};
  return {ok: true, json: async () => { throw new Error('invalid JSON test'); }};
};
const [products, sources] = await Promise.all([module.dataProvider.getProducts(), module.dataProvider.getSources()]);
await module.dataProvider.getProducts();
console.log(JSON.stringify({products, sources, fixtureProducts: module.products, fixtureSources: module.sources,
  demo: module.demo, calls}));
""",
        failure,
    )
    assert result["calls"] == 1
    assert result["demo"]["live"] is False
    assert result["demo"]["liveError"] == error
    assert result["products"] == result["fixtureProducts"] and result["products"]
    assert result["sources"] == result["fixtureSources"] and result["sources"]
    assert {product["supplier"] for product in result["products"]} == {"IEK", "Systeme Electric"}
