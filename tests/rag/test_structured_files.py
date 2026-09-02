"""Structured file adapter tests (Phase 6 exit gates, spec §7.4 / T13).

Covers:
  - xlsx preview: sheets, headers, row counts, empty columns, samples
  - csv preview: delimiter sniffing, header naming, BOM tolerance
  - malformed / encrypted / empty files → StructuredFileError with a repair
    hint, never placeholder text passed downstream
  - markdown rendering carries the real structure to analysis
"""

import io

import openpyxl
import pytest

from app.rag.ingestion.structured import (
    StructuredFileError,
    detect_structured_type,
    preview_csv,
    preview_structured,
    preview_to_markdown,
    preview_xlsx,
)


def _xlsx_bytes(rows_by_sheet: dict[str, list[list[str | None]]]) -> bytes:
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for sheet_name, rows in rows_by_sheet.items():
        sheet = workbook.create_sheet(title=sheet_name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_xlsx_preview_reports_sheets_headers_and_rows():
    content = _xlsx_bytes(
        {
            "花名册": [
                ["姓名", "部门", "入职日期"],
                ["张三", "研发", "2024-01-01"],
                ["李四", "市场", "2023-06-15"],
                ["王五", "研发", "2025-03-02"],
            ],
            "说明": [["备注"], ["测试数据"]],
        }
    )
    preview = preview_xlsx(content, "roster.xlsx")

    assert preview.file_type == "xlsx"
    assert [s.sheet_name for s in preview.sheets] == ["花名册", "说明"]
    first = preview.sheets[0]
    assert first.header_row == ["姓名", "部门", "入职日期"]
    assert first.row_count == 3
    assert first.column_count == 3
    assert first.empty_column_count == 0
    assert first.sample_rows[0] == ["张三", "研发", "2024-01-01"]


def test_xlsx_flags_empty_columns():
    content = _xlsx_bytes({"表1": [["列A", "", "列C"], ["1", "", "3"]]})
    preview = preview_xlsx(content, "has-empty.xlsx")

    sheet = preview.sheets[0]
    assert sheet.empty_column_count == 1
    assert any("空" in note for note in preview.quality_notes)


def test_xlsx_with_only_headers_flags_no_data():
    content = _xlsx_bytes({"表": [["姓名", "部门"]]})
    preview = preview_xlsx(content, "headers-only.xlsx")
    assert preview.sheets[0].row_count == 0
    assert any("没有数据行" in note for note in preview.quality_notes)


def test_invalid_xlsx_raises_with_repair_hint():
    with pytest.raises(StructuredFileError) as excinfo:
        preview_xlsx(b"not a spreadsheet", "bad.xlsx")
    assert "另存为" in excinfo.value.repair_hint


def test_csv_preview_sniffs_delimiter_and_strips_bom():
    raw = "姓名,部门\n张三,研发\n李四,市场".encode("utf-8-sig")
    preview = preview_csv(raw, "people.csv")

    assert preview.file_type == "csv"
    sheet = preview.sheets[0]
    assert sheet.header_row == ["姓名", "部门"]
    assert sheet.row_count == 2


def test_csv_semicolon_delimiter():
    raw = b"a;b\n1;2\n"
    preview = preview_csv(raw, "semi.csv")
    assert preview.sheets[0].header_row == ["a", "b"]
    assert preview.sheets[0].sample_rows[0] == ["1", "2"]


def test_empty_csv_raises_with_repair_hint():
    with pytest.raises(StructuredFileError) as excinfo:
        preview_csv(b"", "empty.csv")
    assert "CSV" in excinfo.value.repair_hint


def test_markdown_carries_structure_to_analysis():
    content = _xlsx_bytes({"表": [["姓名"], ["张三"]]})
    preview = preview_structured(content, "t.xlsx", "xlsx")
    text = preview_to_markdown(preview)

    assert "工作表「表」" in text
    assert "表头：姓名" in text


def test_detect_structured_type():
    assert detect_structured_type("a.xlsx", None) == "xlsx"
    assert detect_structured_type("a.csv", None) == "csv"
    assert detect_structured_type("a.csv", "text/csv") == "csv"
    assert detect_structured_type("a.txt", "text/plain") is None


def test_route_dispatch_rejects_unknown():
    with pytest.raises(StructuredFileError):
        preview_structured(b"x", "f.pdf", "pdf")
