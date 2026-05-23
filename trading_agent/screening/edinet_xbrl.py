"""S4b 深掘り：EDINET 書類本文（XBRL/CSV）から GC注記・監査意見を検出。RESEARCH 領域1。

`scan_disclosure_red_flags` は開示**メタデータ**走査だが、本モジュールは**書類本文**を見る：
EDINET API v2 の `documents/{docID}?type=5`（ZIP of CSV＝XBRLの要素ID/値）をDLして、
継続企業の前提(GC)注記の有無・監査意見の種別を判定する。要EDINETキー。
検出は純粋関数（要素ID/値の照合のみ・LLM非関与）。DLは注入可能（テストはネット非依存）。
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable

# GC注記の XBRL 要素ID（要素IDにこの語を含む）。値が非空＝注記あり。
_GC_ELEMENT = "GoingConcern"
# 監査意見の警戒種別（値に含まれれば赤）
_AUDIT_ADVERSE = ("不適正", "意見不表明", "限定付")
_AUDIT_ELEMENT_HINT = ("AuditOpinion", "監査意見", "OpinionType")
# 空・プレースホルダ扱い
_EMPTY = {"", "-", "－", "null", "NaN", "nan", "None"}

Downloader = Callable[[str], bytes]  # docID → ZIP バイト列


def detect_disclosure_flags_from_xbrl(rows: list[dict]) -> list[str]:
    """XBRL 行（{element_id, value}）から GC注記・監査意見の赤フラグを抽出。"""
    flags: list[str] = []
    for r in rows:
        eid = str(r.get("element_id", ""))
        val = str(r.get("value", "") or "").strip()
        if not val or val in _EMPTY:
            continue
        if _GC_ELEMENT in eid and "継続企業の前提に関する注記（XBRL）" not in flags:
            flags.append("継続企業の前提に関する注記（XBRL）")
        if any(h in eid for h in _AUDIT_ELEMENT_HINT) and any(a in val for a in _AUDIT_ADVERSE):
            label = f"監査意見：{val[:20]}"
            if label not in flags:
                flags.append(label)
    return flags


def _decode(raw: bytes) -> str:
    # UTF-16 は BOM で判定（cp932 を utf-16 で誤デコードして文字化けするのを防ぐ）
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    # BOM 無し：cp932/utf-8 を試し、妥当（要素ID か タブを含む）な結果を採る
    for enc in ("cp932", "utf-8-sig", "utf-8", "utf-16"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if "要素ID" in text or "\t" in text:
            return text
    return raw.decode("utf-8", errors="ignore")


def parse_edinet_csv_zip(data: bytes) -> list[dict]:
    """EDINET の type=5 ZIP（タブ区切りCSV群）を {element_id, value} 行に。"""
    rows: list[dict] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return rows
    with zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            lines = _decode(zf.read(name)).splitlines()
            if not lines:
                continue
            header = lines[0].split("\t")
            eid_i = header.index("要素ID") if "要素ID" in header else 0
            val_i = header.index("値") if "値" in header else (len(header) - 1)
            for line in lines[1:]:
                fields = line.split("\t")
                if len(fields) <= max(eid_i, val_i):
                    continue
                rows.append({"element_id": fields[eid_i], "value": fields[val_i]})
    return rows


def fetch_document_flags(doc_id: str, *, downloader: Downloader) -> list[str]:
    """docID の本文をDL・解析し、GC/監査の赤フラグを返す。DL失敗・不正ZIPは空。"""
    try:
        data = downloader(doc_id)
    except Exception:
        return []
    return detect_disclosure_flags_from_xbrl(parse_edinet_csv_zip(data))


def edinet_csv_downloader(api_key: str) -> Downloader:
    """公式EDINET v2 から type=5（CSV）ZIP を取る downloader（要キー）。"""

    def download(doc_id: str) -> bytes:
        import httpx

        resp = httpx.get(
            f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
            params={"type": "5"},
            headers={"Ocp-Apim-Subscription-Key": api_key},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.content

    return download
