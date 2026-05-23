"""S4b 深掘り：EDINET XBRL/CSV から GC注記・監査意見を検出するテスト（ネット非依存）。"""

from __future__ import annotations

import io
import zipfile

from trading_agent.screening.edinet_xbrl import (
    detect_disclosure_flags_from_xbrl,
    fetch_document_flags,
    parse_edinet_csv_zip,
)


def _zip(rows: list[tuple[str, str]], *, encoding: str = "utf-16") -> bytes:
    """EDINET風のタブ区切りCSV（要素ID/項目名/値）をZIP化。"""
    header = "要素ID\t項目名\t値"
    body = "\n".join(f"{eid}\t名称\t{val}" for eid, val in rows)
    text = header + "\n" + body
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("XBRL_TO_CSV/doc.csv", text.encode(encoding))
    return buf.getvalue()


class TestDetect:
    def test_gc_note_present(self) -> None:
        rows = [{"element_id": "jpcrp_cor:GoingConcernAssumptionsTextBlock",
                 "value": "当社は継続企業の前提に重要な疑義がある"}]
        assert "継続企業の前提に関する注記（XBRL）" in detect_disclosure_flags_from_xbrl(rows)

    def test_gc_element_empty_no_flag(self) -> None:
        rows = [{"element_id": "jpcrp_cor:GoingConcernAssumptionsTextBlock", "value": "－"}]
        assert detect_disclosure_flags_from_xbrl(rows) == []

    def test_adverse_audit_opinion(self) -> None:
        rows = [{"element_id": "jpaud:OpinionType", "value": "不適正意見"}]
        flags = detect_disclosure_flags_from_xbrl(rows)
        assert any("監査意見" in f for f in flags)

    def test_clean_no_flags(self) -> None:
        rows = [{"element_id": "jpcrp_cor:NetSales", "value": "1000000"},
                {"element_id": "jpaud:OpinionType", "value": "無限定適正意見"}]
        assert detect_disclosure_flags_from_xbrl(rows) == []


class TestParseZip:
    def test_parses_tab_csv(self) -> None:
        data = _zip([("jpcrp_cor:GoingConcernAssumptionsTextBlock", "疑義あり")])
        rows = parse_edinet_csv_zip(data)
        assert rows and rows[0]["element_id"].endswith("GoingConcernAssumptionsTextBlock")
        assert rows[0]["value"] == "疑義あり"

    def test_cp932_encoding(self) -> None:
        data = _zip([("jpaud:OpinionType", "意見不表明")], encoding="cp932")
        assert parse_edinet_csv_zip(data)[0]["value"] == "意見不表明"

    def test_bad_zip_returns_empty(self) -> None:
        assert parse_edinet_csv_zip(b"not a zip") == []


class TestFetchDocumentFlags:
    def test_end_to_end_gc(self) -> None:
        data = _zip([("jpcrp_cor:GoingConcernAssumptionsTextBlock", "継続企業の前提に重要事象")])
        flags = fetch_document_flags("S100ABCD", downloader=lambda _id: data)
        assert any("継続企業" in f for f in flags)

    def test_download_failure_graceful(self) -> None:
        def boom(_id: str) -> bytes:
            raise RuntimeError("net down")

        assert fetch_document_flags("S100ABCD", downloader=boom) == []
