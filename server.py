#!/usr/bin/env python3
"""Local static server + tiny proxy for 서울당서초등학교 공지사항 (school notice board).

The school site's board list endpoint requires a session cookie established
by first visiting the board page, and does not send CORS headers that would
let a browser call it directly with credentials. So we fetch + parse it here
server-side and expose the result as same-origin JSON at /api/notices.
"""
import html as html_module
import http.server
import json
import re
import subprocess
import tempfile
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode, urlparse, parse_qs

PORT = int(os.environ.get("PORT", 8080))
NOTICE_BASE = "https://dangseo.sen.es.kr"
LIST_AJAX_PATH = "/dggb/module/board/selectBoardListAjax.do"

# Timetable: NEIS's own elsTimetable API silently caps responses at 5 rows
# per day even when list_total_count reports more (confirmed by comparing
# identical queries with different pSize values), so any 6th-period class
# never comes through. koreacharts.com republishes the same NEIS data as a
# plain server-rendered page per date and doesn't have that truncation.
KOREACHARTS_BASE = "https://school.koreacharts.com"
KOREACHARTS_SCHOOL_ID = "B000001998"  # 서울당서초등학교
_tt_page_cache = {}  # date_str -> {"html":..., "ts":...}
TT_PAGE_CACHE_TTL = 21600  # 6 hours

# Board configs: 공지사항 / 가정통신문(학교) / 가정통신문(교육청).
# The 교육청 board uses a different detail-view ajax endpoint (selectBoardSenDetailAjax.do)
# and its list rows call fnSenView(nttId) instead of fnView(bbsId, nttId).
BOARDS = {
    "notice": {
        "bbsId": "BBSMSTR_000000010346",
        "bbsTyCode": "notice",
        "menuPath": "/73506/subMenu.do",
        "detailAjaxPath": "/dggb/module/board/selectBoardDetailAjax.do",
    },
    "dliv": {
        "bbsId": "BBSMSTR_000000010347",
        "bbsTyCode": "dliv",
        "menuPath": "/73507/subMenu.do",
        "detailAjaxPath": "/dggb/module/board/selectBoardDetailAjax.do",
    },
    "sliv": {
        "bbsId": "BBS_0000000000975481",
        "bbsTyCode": "sliv",
        "menuPath": "/193415/subMenu.do",
        "detailAjaxPath": "/dggb/module/board/selectBoardSenDetailAjax.do",
    },
}

_list_cache = {}  # board_key -> {"data":..., "ts":...}
LIST_CACHE_TTL = 3600  # 1 hour
_newsletter_cache = {"data": None, "ts": 0}
_detail_cache = {}  # "board:nttId" -> {"data":..., "ts":...}
DETAIL_CACHE_TTL = 3600  # 1 hour


class NoticeRowParser(HTMLParser):
    """Parses the board list <table> fragment into rows of cell text."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.cur_row_cells = None
        self.cur_td_text = None
        self.cur_ntt_id = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "tr":
            self.cur_row_cells = []
        elif tag == "td":
            self.cur_td_text = []
        elif tag == "a" and d.get("onclick") and ("fnView" in d["onclick"] or "fnSenView" in d["onclick"]):
            onclick = d["onclick"]
            m = re.search(r"fnView\('([^']*)',\s*'(\d+)'\)", onclick)
            if m:
                self.cur_ntt_id = m.group(2)
            else:
                m2 = re.search(r"fnSenView\('(\d+)'\)", onclick)
                if m2:
                    self.cur_ntt_id = m2.group(1)

    def handle_endtag(self, tag):
        if tag == "td" and self.cur_td_text is not None:
            text = " ".join("".join(self.cur_td_text).split())
            self.cur_row_cells.append(text)
            self.cur_td_text = None
        elif tag == "tr" and self.cur_row_cells is not None:
            if self.cur_row_cells:
                self.rows.append({"cells": self.cur_row_cells, "nttId": self.cur_ntt_id})
            self.cur_row_cells = None
            self.cur_ntt_id = None

    def handle_data(self, data):
        if self.cur_td_text is not None:
            self.cur_td_text.append(data)


def _curl_post_with_session(menu_path, post_path, payload_dict):
    """Shell out to curl for this one host: the school site's TLS handshake
    (legacy signature scheme) is rejected by Python's bundled OpenSSL on some
    platforms, but curl connects fine. `-k` skips cert verification as a
    robustness fallback across hosting environments — this only ever reads
    public, non-sensitive school bulletin data, never sends credentials. All
    payload values are fixed server-side or numeric ntt ids, so this is not
    command-injectable. `menu_path` primes the session so the site's board
    context (which bbsId is "active") matches the board we're requesting."""
    with tempfile.NamedTemporaryFile(prefix="dangseo_cookies_", suffix=".txt") as cookie_file:
        subprocess.run(
            [
                "curl", "-s", "-L", "-k",
                "-c", cookie_file.name,
                "-A", "Mozilla/5.0",
                NOTICE_BASE + menu_path,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=True,
        )

        payload = urlencode(payload_dict)
        result = subprocess.run(
            [
                "curl", "-s", "-k",
                "-b", cookie_file.name,
                "-A", "Mozilla/5.0",
                "-H", "Content-Type: application/x-www-form-urlencoded",
                "-H", "Referer: " + NOTICE_BASE + menu_path,
                "--data", payload,
                NOTICE_BASE + post_path,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15, check=True,
        )
        return result.stdout.decode("utf-8", errors="replace")


def fetch_board_list(board_key, count=10):
    cfg = BOARDS[board_key]
    now = time.time()
    cached = _list_cache.get(board_key)
    if cached is not None and now - cached["ts"] < LIST_CACHE_TTL:
        return cached["data"]

    html_text = _curl_post_with_session(cfg["menuPath"], LIST_AJAX_PATH, {
        "bbsId": cfg["bbsId"],
        "bbsTyCode": cfg["bbsTyCode"],
        "customRecordCountPerPage": str(count),
        "pageIndex": "1",
        "searchCondition": "",
        "searchKeyword": "",
        "checkNttId": "",
        "mvmnReturnUrl": "",
    })

    parser = NoticeRowParser()
    parser.feed(html_text)

    items = []
    for row in parser.rows:
        cells = row["cells"]
        if len(cells) < 5 or not row["nttId"]:
            continue
        num_or_flag, title, writer, date, views = cells[:5]
        items.append({
            "board": board_key,
            "nttId": row["nttId"],
            "isNotice": num_or_flag.strip() == "공지",
            "title": title.strip(),
            "writer": writer.strip(),
            "date": date.strip(),
            "views": views.strip(),
        })

    _list_cache[board_key] = {"data": items, "ts": now}
    return items


def fetch_notices():
    items = fetch_board_list("notice")
    return {"schoolName": "서울당서초등학교", "updatedAt": int(time.time()), "items": items}


def fetch_newsletters():
    now = time.time()
    if _newsletter_cache["data"] is not None and now - _newsletter_cache["ts"] < LIST_CACHE_TTL:
        return _newsletter_cache["data"]

    school_items = fetch_board_list("dliv")
    office_items = fetch_board_list("sliv")

    combined = []
    for it in school_items:
        combined.append({**it, "displayTitle": it["title"]})
    for it in office_items:
        combined.append({**it, "displayTitle": it["title"] + " (교육청)"})
    combined.sort(key=lambda x: x["date"], reverse=True)

    result = {"schoolName": "서울당서초등학교", "updatedAt": int(now), "items": combined}
    _newsletter_cache["data"] = result
    _newsletter_cache["ts"] = now
    return result


def _fetch_koreacharts_page(date_str):
    now = time.time()
    cached = _tt_page_cache.get(date_str)
    if cached is not None and now - cached["ts"] < TT_PAGE_CACHE_TTL:
        return cached["html"]

    url = f"{KOREACHARTS_BASE}/timetable/{KOREACHARTS_SCHOOL_ID}/{date_str}.html"
    result = subprocess.run(
        ["curl", "-s", "-k", "-A", "Mozilla/5.0", url],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=15, check=True,
    )
    html_text = result.stdout.decode("utf-8", errors="replace")
    _tt_page_cache[date_str] = {"html": html_text, "ts": now}
    return html_text


def _extract_class_periods(html_text, grade, class_nm):
    """Each date page lists every class in the school as its own box; find
    the one matching this grade/class and pull out its (period, subject) rows."""
    boxes = re.split(r'<h2 class="box-title">', html_text)
    for box in boxes[1:]:
        m_grade = re.search(r'학년</th>\s*<td class="text-center">(\d+)</td>', box)
        m_class = re.search(r'학급명</th>\s*<td class="text-center">(\d+)</td>', box)
        if not (m_grade and m_class):
            continue
        if m_grade.group(1) != str(grade) or m_class.group(1) != str(class_nm):
            continue
        periods = re.findall(r'(\d+)교시\s*</td>\s*<td class="text-center">([^<]+)</td>', box)
        return [{"period": int(p), "subject": s.strip()} for p, s in periods]
    return []


def fetch_day_timetable(date_str, grade, class_nm):
    html_text = _fetch_koreacharts_page(date_str)
    return _extract_class_periods(html_text, grade, class_nm)


def fetch_week_timetable(monday_str, grade, class_nm):
    monday = datetime.strptime(monday_str, "%Y%m%d")
    date_strs = [(monday + timedelta(days=i)).strftime("%Y%m%d") for i in range(5)]

    # Fetching the 5 weekday pages sequentially was slow enough (each is a
    # separate curl round-trip to koreacharts.com) to blow past the hosting
    # platform's request timeout, so fetch them concurrently instead.
    items = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_day_timetable, d, grade, class_nm): d for d in date_strs}
        results = {}
        for future in as_completed(futures):
            date_str = futures[future]
            results[date_str] = future.result()

    for date_str in date_strs:
        for p in results.get(date_str, []):
            items.append({"date": date_str, "period": p["period"], "subject": p["subject"]})
    return {"items": items}


def _extract_balanced_div(text, marker):
    """Return the inner HTML of a <div ...> block starting at `marker`,
    correctly handling nested <div> tags inside (e.g. rich notice content)."""
    start = text.find(marker)
    if start == -1:
        return ""
    tag_end = text.find(">", start)
    if tag_end == -1:
        return ""
    pos = tag_end + 1
    depth = 1
    for m in re.finditer(r"<div\b[^>]*>|</div\s*>", text[pos:], re.IGNORECASE):
        if m.group(0).lower().startswith("</div"):
            depth -= 1
            if depth == 0:
                return text[pos:pos + m.start()]
        else:
            depth += 1
    return text[pos:]


def _extract_simple_div(text, after_marker):
    idx = text.find(after_marker)
    if idx == -1:
        return ""
    m = re.search(r"<div[^>]*>(.*?)</div>", text[idx:], re.DOTALL)
    if not m:
        return ""
    return " ".join(m.group(1).split())


def fetch_notice_detail(board_key, ntt_id):
    cfg = BOARDS[board_key]
    cache_key = f"{board_key}:{ntt_id}"
    now = time.time()
    cached = _detail_cache.get(cache_key)
    if cached is not None and now - cached["ts"] < DETAIL_CACHE_TTL:
        return cached["data"]

    html_text = _curl_post_with_session(cfg["menuPath"], cfg["detailAjaxPath"], {
        "bbsId": cfg["bbsId"],
        "nttId": ntt_id,
        "bbsTyCode": cfg["bbsTyCode"],
        "customRecordCountPerPage": "10",
        "pageIndex": "1",
        "searchCondition": "",
        "searchKeyword": "",
        "checkNttId": "",
        "mvmnReturnUrl": "",
    })

    title = _extract_simple_div(html_text, "제목</th>")
    writer = _extract_simple_div(html_text, "이름</th>")
    date = _extract_simple_div(html_text, "등록일</th>")
    content_html = _extract_balanced_div(html_text, '<div class="content">')
    # strip scripts as a light safety measure; this content is otherwise trusted (school site)
    content_html = re.sub(r"<script\b[^>]*>.*?</script>", "", content_html, flags=re.IGNORECASE | re.DOTALL)

    attachments = []
    for name, size, atch_id, file_sn in re.findall(
        r'serverFileObj\["name"\]\s*=\s*"([^"]*)";\s*'
        r'serverFileObj\["size"\]\s*=\s*"(\d+)";\s*'
        r'serverFileObj\["atchFileId"\]\s*=\s*"([^"]*)";\s*'
        r'serverFileObj\["fileSn"\]\s*=\s*"([^"]*)"',
        html_text,
    ):
        attachments.append({
            "name": name,
            "size": int(size),
            "atchFileId": atch_id,
            "fileSn": file_sn,
        })

    result = {
        "nttId": ntt_id,
        "board": board_key,
        "title": title or "(제목 없음)",
        "writer": writer,
        "date": date,
        "contentHtml": content_html.strip(),
        "attachments": attachments,
    }
    _detail_cache[cache_key] = {"data": result, "ts": now}
    return result


def render_notice_detail_page(detail):
    def esc(s):
        return html_module.escape(s, quote=True)

    attachments_html = ""
    if detail["attachments"]:
        items = "".join(
            f'<li><a href="{NOTICE_BASE}/dggb/board/boardFile/downFile.do?'
            f'atchFileId={esc(a["atchFileId"])}&fileSn={esc(a["fileSn"])}" '
            f'target="_blank" rel="noopener">📎 {esc(a["name"])}</a> '
            f'<span class="size">({a["size"]/1024:.1f} KB)</span></li>'
            for a in detail["attachments"]
        )
        attachments_html = f'<ul class="attachments">{items}</ul>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(detail["title"])}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Malgun Gothic", sans-serif;
    max-width: 720px;
    margin: 0 auto;
    padding: 32px 24px 60px;
    color: #1f2937;
    background: #ffffff;
    line-height: 1.7;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1f2937; color: #f3f4f6; }}
    a {{ color: #60a5fa; }}
    .meta, .size {{ color: #9ca3af !important; }}
    .attachments a {{ color: #60a5fa; }}
    hr {{ border-color: #374151 !important; }}
  }}
  h1 {{ font-size: 1.4rem; margin-bottom: 8px; }}
  .meta {{ color: #6b7280; font-size: 0.85rem; margin-bottom: 20px; }}
  hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 20px 0; }}
  .content img {{ max-width: 100%; height: auto; }}
  .content table {{ max-width: 100%; overflow-x: auto; display: block; }}
  .attachments {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid #e5e7eb; list-style: none; }}
  .attachments li {{ margin-bottom: 8px; }}
  .size {{ color: #6b7280; font-size: 0.8rem; }}
  .back-link {{ display: inline-block; margin-top: 32px; font-size: 0.85rem; }}
</style>
</head>
<body>
  <h1>{esc(detail["title"])}</h1>
  <div class="meta">{esc(detail["writer"])} · {esc(detail["date"])}</div>
  <hr>
  <div class="content">{detail["contentHtml"]}</div>
  {attachments_html}
  <a class="back-link" href="{NOTICE_BASE}{BOARDS[detail["board"]]["menuPath"]}" target="_blank" rel="noopener">학교 홈페이지 게시판에서 보기 →</a>
</body>
</html>"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/notices":
            try:
                self._send_json(fetch_notices())
            except Exception as e:
                self._send_json({"error": str(e)}, status=502)
            return

        if parsed.path == "/api/newsletters":
            try:
                self._send_json(fetch_newsletters())
            except Exception as e:
                self._send_json({"error": str(e)}, status=502)
            return

        if parsed.path == "/api/timetable":
            qs = parse_qs(parsed.query)
            grade = (qs.get("grade") or [""])[0]
            class_nm = (qs.get("classNm") or [""])[0]
            monday_str = (qs.get("monday") or [""])[0]
            valid = (
                grade.isdigit() and class_nm.isdigit()
                and re.fullmatch(r"\d{8}", monday_str or "")
            )
            if not valid:
                self._send_json({"error": "잘못된 요청입니다."}, status=400)
                return
            try:
                self._send_json(fetch_week_timetable(monday_str, grade, class_nm))
            except Exception as e:
                self._send_json({"error": str(e)}, status=502)
            return

        if parsed.path == "/api/notice_detail":
            qs = parse_qs(parsed.query)
            ntt_id = (qs.get("nttId") or [""])[0]
            board_key = (qs.get("board") or ["notice"])[0]
            if not ntt_id.isdigit() or board_key not in BOARDS:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("잘못된 요청입니다.".encode("utf-8"))
                return
            try:
                detail = fetch_notice_detail(board_key, ntt_id)
                body = render_notice_detail_page(detail).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = f"<p>공지 내용을 불러오지 못했습니다: {html_module.escape(str(e))}</p>".encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return

        return super().do_GET()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Serving on http://0.0.0.0:{PORT} (accessible via this machine's LAN IP)")
    httpd.serve_forever()
