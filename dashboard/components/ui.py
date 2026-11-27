"""디자인 시스템 — 에디토리얼 워밍 톤 + 고밀도 전략판.

HTML 조각은 문자열로 만들고(`*_html`), 화면 출력은 `st.html` 로 한다.
사용자/DB 텍스트는 반드시 `esc()` 를 거친다.
"""

from __future__ import annotations

import html
from typing import Iterable

import streamlit as st

from lib.transforms import DASH, to_num, truncate

TRACK_ACCENT = {"A": "#B65A3C", "B": "#2E7268", None: "#16140F"}
TRACK_NAME = {"A": "Track A · 셀럽 유튜브", "B": "Track B · 패션+유행", None: "전략 브리핑"}

_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
  --bg:#FAFAF7; --bg-2:#F2F1EB; --bg-3:#E9E7DF; --surface:#FFFFFF; --surface-2:#F6F5EF;
  --ink:#16140F; --ink-2:#3D3A33; --ink-3:#6C685E; --ink-4:#9E9A8E;
  --line:#E5E2D7; --line-2:#D7D3C5;
  --up:#3F8A5C; --up-bg:#EAF4ED; --down:#B8442F; --down-bg:#FAECE8;
  --info:#5868A8; --info-bg:#EDEFF8; --warn:#B9861D; --warn-bg:#FBF3E1;
  --neutral:#6C685E; --neutral-bg:#EFEDE6; --muted:#9E9A8E; --muted-bg:#F2F1EB;
  --track-a:#B65A3C; --track-a-bg:#F7EAE4; --track-b:#2E7268; --track-b-bg:#E3EFEC;
  --mono:'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;
  --sans:'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
}

html, body, .stApp, [data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"],
button, input, textarea, select { font-family: var(--sans) !important; -webkit-font-smoothing: antialiased; }
code, pre, .mono { font-family: var(--mono) !important; }

/* ── Chrome: 헤더는 투명하게 유지(사이드바 재오픈 버튼이 헤더 안에 있음) ── */
header[data-testid="stHeader"] { background: transparent !important; box-shadow: none !important; }
[data-testid="stDecoration"], [data-testid="stAppDeployButton"], [data-testid="stToolbarActions"] { display: none !important; }
[data-testid="stExpandSidebarButton"], [data-testid="stSidebarCollapseButton"] button {
  color: var(--ink-2) !important; background: var(--surface) !important;
  border: 1px solid var(--line) !important; border-radius: 8px !important;
}
[data-testid="stExpandSidebarButton"]:hover { border-color: var(--ink-3) !important; }

.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background: var(--bg) !important; }
[data-testid="stMainBlockContainer"] { padding: 2.2rem 2.4rem 4rem !important; max-width: 1480px !important; }
@media (max-width: 720px) { [data-testid="stMainBlockContainer"] { padding: 1.4rem 1rem 3rem !important; } }

/* ── Sidebar ── */
[data-testid="stSidebar"] { background: var(--bg-2) !important; border-right: 1px solid var(--line) !important; }
[data-testid="stSidebarNav"] { padding-top: 4px; }
[data-testid="stNavSectionHeader"] {
  font-size: 10.5px !important; letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-3) !important; font-weight: 600 !important; margin-top: 14px !important;
}
[data-testid="stSidebarNavLink"] { border-radius: 6px !important; padding: 4px 10px !important; }
[data-testid="stSidebarNavLink"] span { font-size: 13px !important; color: var(--ink-2) !important; }
[data-testid="stSidebarNavLink"][aria-current="page"] { background: var(--surface) !important; box-shadow: inset 0 0 0 1px var(--line); }
[data-testid="stSidebarNavLink"][aria-current="page"] span { color: var(--ink) !important; font-weight: 600 !important; }

/* ── Widgets ── */
[data-testid="stWidgetLabel"] p { font-size: 11px !important; color: var(--ink-3) !important; font-weight: 500 !important; letter-spacing: .02em; }
[data-baseweb="select"] > div, [data-baseweb="input"] > div { border-radius: 6px !important; background: var(--surface) !important; border-color: var(--line) !important; }
[data-baseweb="tag"] { background: var(--bg-2) !important; color: var(--ink-2) !important; border-radius: 4px !important; }
.stTabs [data-baseweb="tab-list"] { gap: 2px; border-bottom: 1px solid var(--line); }
.stTabs [data-baseweb="tab"] { font-size: 13px !important; padding: 8px 14px !important; }
.stTabs [aria-selected="true"] { font-weight: 600 !important; }
[data-testid="stExpander"] details { border: 1px solid var(--line) !important; border-radius: 8px !important; background: var(--surface) !important; }
[data-testid="stExpander"] summary p { font-size: 13px !important; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--line) !important; border-radius: 10px !important; }
[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
[data-testid="stVegaLiteChart"] { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 10px 6px; }
.stAlert { border-radius: 8px !important; }

/* ── Page header ── */
.ds-head { display:flex; justify-content:space-between; align-items:flex-end; gap:24px; flex-wrap:wrap;
  padding: 2px 0 18px; border-bottom: 1px solid var(--line); margin-bottom: 22px; }
.ds-eyebrow { display:flex; gap:8px; align-items:center; font-family:var(--mono); font-size:11px; color:var(--ink-3); margin-bottom:8px; }
.ds-eyebrow .chip { padding:2px 8px; border-radius:999px; font-weight:600; letter-spacing:.02em; }
.ds-title { font-size: 26px; font-weight: 700; letter-spacing: -.025em; color: var(--ink); line-height: 1.2; }
.ds-lede { font-size: 13.5px; color: var(--ink-3); margin-top: 6px; max-width: 760px; line-height: 1.6; }
.ds-meta { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.ds-pill { display:inline-flex; gap:6px; align-items:center; background:var(--surface); border:1px solid var(--line);
  padding:4px 10px; border-radius:999px; font-family:var(--mono); font-size:11px; color:var(--ink-2); white-space:nowrap; }
.ds-dot { width:6px; height:6px; border-radius:50%; display:inline-block; }

/* ── Section ── */
.ds-sec { display:flex; justify-content:space-between; align-items:baseline; gap:12px; margin: 30px 0 12px; }
.ds-sec h3 { font-size: 15px !important; font-weight: 650 !important; margin: 0 !important; padding: 0 !important; color: var(--ink); letter-spacing: -.01em; }
.ds-sec .sub { font-size: 12px; color: var(--ink-3); margin-top: 3px; }
.ds-sec .right { font-family: var(--mono); font-size: 11px; color: var(--ink-4); white-space: nowrap; }
.ds-sec .kicker { font-family: var(--mono); font-size: 10.5px; color: var(--ink-4); margin-right: 8px; }

/* ── KPI strip ── */
.ds-kpis { display:grid; gap:1px; background:var(--line); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
.ds-kpi { background:var(--surface); padding:14px 16px 13px; min-width:0; }
.ds-kpi .l { font-size:11px; color:var(--ink-3); font-weight:500; letter-spacing:.02em; }
.ds-kpi .v { font-family:var(--mono); font-size:23px; font-weight:500; letter-spacing:-.03em; color:var(--ink); margin-top:4px; line-height:1.15; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ds-kpi .s { font-size:11px; color:var(--ink-4); margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
@media (max-width: 900px) { .ds-kpis { grid-template-columns: repeat(2, 1fr) !important; } }

/* ── Badge ── */
.ds-b { display:inline-flex; align-items:center; gap:4px; padding:2px 7px; border-radius:4px; font-family:var(--mono);
  font-size:10.5px; font-weight:500; white-space:nowrap; letter-spacing:.01em; line-height:1.6; }
.ds-badges { display:flex; flex-wrap:wrap; gap:5px; }

/* ── Meter ── */
.ds-meters { display:grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap:8px 14px; }
.ds-m .top { display:flex; justify-content:space-between; font-size:11px; color:var(--ink-3); margin-bottom:4px; }
.ds-m .top b { font-family:var(--mono); font-weight:500; color:var(--ink); }
.ds-m .bar { height:4px; background:var(--bg-3); border-radius:2px; overflow:hidden; }
.ds-m .bar i { display:block; height:100%; border-radius:2px; }

/* ── Cards ── */
.ds-card { background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:16px 18px; height:100%;
  display:flex; flex-direction:column; gap:10px; position:relative; }
.ds-card.accent { border-top: 3px solid var(--accent, var(--ink)); }
.ds-card .row { display:flex; justify-content:space-between; align-items:center; gap:10px; }
.ds-rank { font-family:var(--mono); font-size:11px; color:var(--ink-4); }
.ds-big { font-family:var(--mono); font-size:28px; font-weight:500; letter-spacing:-.04em; line-height:1; color:var(--ink); }
.ds-big small { font-size:11px; color:var(--ink-4); letter-spacing:0; margin-left:3px; }
.ds-ctitle { font-size:14.5px; font-weight:650; color:var(--ink); line-height:1.45; letter-spacing:-.01em; }
.ds-ctitle a { color:inherit !important; text-decoration:none; }
.ds-ctitle a:hover { text-decoration:underline; text-underline-offset:3px; }
.ds-csub { font-size:11.5px; color:var(--ink-3); }
.ds-reason { font-size:12.5px; color:var(--ink-2); line-height:1.65; border-top:1px dashed var(--line); padding-top:9px; }
.ds-grid { display:grid; gap:12px; }
.ds-grid.c2 { grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
.ds-grid.c3 { grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }

/* ── Lists / callouts ── */
.ds-list { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 16px; height:100%; }
.ds-list .h { font-size:11px; font-weight:600; letter-spacing:.04em; margin-bottom:6px; }
.ds-list .it { display:flex; gap:9px; padding:7px 0; border-top:1px solid var(--bg-2); font-size:12.5px; color:var(--ink-2); line-height:1.55; }
.ds-list .it:first-of-type { border-top:none; }
.ds-list .mk { font-family:var(--mono); font-size:11px; min-width:14px; padding-top:1px; }
.ds-call { display:flex; gap:10px; align-items:flex-start; padding:11px 14px; border-radius:10px; font-size:12.5px; line-height:1.6; color:var(--ink-2); }
.ds-call .lab { font-family:var(--mono); font-size:10px; padding:1px 6px; border-radius:3px; background:rgba(0,0,0,.06); white-space:nowrap; margin-top:2px; }
.ds-empty { border:1px dashed var(--line-2); border-radius:10px; padding:22px; text-align:center; color:var(--ink-3); font-size:13px; background:var(--surface-2); }
.ds-empty .hint { font-size:11.5px; color:var(--ink-4); margin-top:4px; }

/* ── Hero ── */
.ds-hero { background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:20px 22px; display:grid;
  grid-template-columns: minmax(0,1fr) auto; gap:20px; align-items:center; }
.ds-hero .q { font-size:16px; line-height:1.7; color:var(--ink); letter-spacing:-.01em; }
.ds-hero .q b { font-weight:650; }
@media (max-width: 900px) { .ds-hero { grid-template-columns: 1fr; } }
.ds-gauge { text-align:right; min-width:120px; }
.ds-gauge .n { font-family:var(--mono); font-size:38px; font-weight:500; letter-spacing:-.05em; line-height:1; }
.ds-gauge .l { font-size:11px; color:var(--ink-3); margin-top:4px; }

/* ── Footer ── */
.ds-foot { margin-top:44px; padding-top:14px; border-top:1px solid var(--line); font-size:10.5px; color:var(--ink-4); line-height:1.9; }
.ds-foot b { font-family:var(--mono); font-weight:500; color:var(--ink-3); }

/* ── Sidebar brand ── */
.ds-brand { display:flex; gap:10px; align-items:center; padding:2px 2px 14px; border-bottom:1px solid var(--line); margin-bottom:6px; }
.ds-brand .logo { width:30px; height:30px; border-radius:8px; background:var(--ink); color:var(--bg); display:grid; place-items:center;
  font-family:var(--mono); font-weight:600; font-size:13px; }
.ds-brand .t { font-weight:650; font-size:13.5px; color:var(--ink); letter-spacing:-.01em; }
.ds-brand .s { font-size:11px; color:var(--ink-3); }
.ds-health { font-family:var(--mono); font-size:10.5px; color:var(--ink-3); line-height:2; }
.ds-health .r { display:flex; justify-content:space-between; gap:8px; }
</style>
"""

TONES = {
    "up": ("var(--up)", "var(--up-bg)"),
    "down": ("var(--down)", "var(--down-bg)"),
    "info": ("var(--info)", "var(--info-bg)"),
    "warn": ("var(--warn)", "var(--warn-bg)"),
    "neutral": ("var(--neutral)", "var(--neutral-bg)"),
    "muted": ("var(--muted)", "var(--muted-bg)"),
    "ink": ("var(--surface)", "var(--ink)"),
    "A": ("var(--track-a)", "var(--track-a-bg)"),
    "B": ("var(--track-b)", "var(--track-b-bg)"),
}


def esc(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def inject_css() -> None:
    st.html(_CSS)


def render(html_str: str) -> None:
    st.html(html_str)


# ─────────────────────────────────────────────────────────
# 원자 요소
# ─────────────────────────────────────────────────────────
def badge_html(text, tone: str = "neutral") -> str:
    fg, bg = TONES.get(tone, TONES["neutral"])
    return f'<span class="ds-b" style="color:{fg};background:{bg}">{esc(text)}</span>'


def badges_html(items: Iterable[tuple[str, str]]) -> str:
    items = list(items)
    if not items:
        return ""
    return '<div class="ds-badges">' + "".join(badge_html(t, tone) for t, tone in items) + "</div>"


def link_html(url, text, title_limit: int = 90) -> str:
    label = esc(truncate(text, title_limit)) or DASH
    if url and str(url).startswith(("http://", "https://")):
        return f'<a href="{esc(url)}" target="_blank" rel="noopener">{label}</a>'
    return label


def video_url(video_id, url=None) -> str | None:
    if url:
        return str(url)
    return f"https://youtu.be/{video_id}" if video_id else None


def meter_html(label, value, max_value: float = 100, invert: bool = False, fmt: str = "int") -> str:
    """수평 점수 미터. invert=True 면 높을수록 경고색(리스크·난이도)."""
    n = to_num(value)
    pct = 0 if n is None else max(0.0, min(1.0, n / max_value)) * 100
    if n is None:
        color, shown = "var(--line-2)", DASH
    else:
        hi = pct >= 66
        lo = pct < 40
        if invert:
            color = "var(--down)" if hi else ("var(--warn)" if not lo else "var(--up)")
        else:
            color = "var(--up)" if hi else ("var(--ink-2)" if not lo else "var(--ink-4)")
        shown = f"{n:.2f}" if fmt == "float" else f"{int(round(n))}"
    return (f'<div class="ds-m"><div class="top"><span>{esc(label)}</span><b>{shown}</b></div>'
            f'<div class="bar"><i style="width:{pct:.0f}%;background:{color}"></i></div></div>')


def meters_html(items: Iterable[tuple], max_value: float = 100, fmt: str = "int") -> str:
    """items: (label, value[, invert])"""
    parts = []
    for item in items:
        label, value = item[0], item[1]
        invert = item[2] if len(item) > 2 else False
        parts.append(meter_html(label, value, max_value=max_value, invert=invert, fmt=fmt))
    return '<div class="ds-meters">' + "".join(parts) + "</div>"


# ─────────────────────────────────────────────────────────
# 레이아웃 블록
# ─────────────────────────────────────────────────────────
def page_header(title: str, lede: str = "", *, track: str | None = None, code: str = "",
                meta: Iterable[str] = ()) -> None:
    accent = TRACK_ACCENT.get(track, TRACK_ACCENT[None])
    fg, bg = TONES.get(track or "ink", TONES["ink"])
    chip = f'<span class="chip" style="color:{fg};background:{bg}">{esc(TRACK_NAME.get(track))}</span>'
    code_html = f"<span>{esc(code)}</span>" if code else ""
    meta_html = "".join(meta)
    render(
        f'<div class="ds-head" style="--accent:{accent}"><div>'
        f'<div class="ds-eyebrow">{chip}{code_html}</div>'
        f'<div class="ds-title">{esc(title)}</div>'
        + (f'<div class="ds-lede">{esc(lede)}</div>' if lede else "")
        + f'</div><div class="ds-meta">{meta_html}</div></div>'
    )


def pill_html(text, tone: str | None = None) -> str:
    dot = ""
    if tone:
        fg, _ = TONES.get(tone, TONES["neutral"])
        dot = f'<span class="ds-dot" style="background:{fg}"></span>'
    return f'<span class="ds-pill">{dot}{esc(text)}</span>'


def section(title: str, sub: str = "", *, right: str = "", kicker: str = "") -> None:
    k = f'<span class="kicker">{esc(kicker)}</span>' if kicker else ""
    s = f'<div class="sub">{esc(sub)}</div>' if sub else ""
    r = f'<div class="right">{esc(right)}</div>' if right else ""
    render(f'<div class="ds-sec"><div><h3>{k}{esc(title)}</h3>{s}</div>{r}</div>')


def kpi_strip(items: list[dict]) -> None:
    if not items:
        return
    cells = []
    for it in items:
        tone = it.get("tone")
        color = f' style="color:{TONES[tone][0]}"' if tone in TONES and tone != "ink" else ""
        sub = f'<div class="s">{esc(it.get("sub"))}</div>' if it.get("sub") else ""
        cells.append(f'<div class="ds-kpi"><div class="l">{esc(it.get("label"))}</div>'
                     f'<div class="v"{color}>{esc(it.get("value", DASH))}</div>{sub}</div>')
    render(f'<div class="ds-kpis" style="grid-template-columns:repeat({len(items)},minmax(0,1fr))">'
           + "".join(cells) + "</div>")


def list_block_html(title: str, items: Iterable, tone: str = "neutral", marker: str = "num",
                    limit: int = 6, empty: str = "데이터 없음") -> str:
    fg, _ = TONES.get(tone, TONES["neutral"])
    items = [i for i in items if i][:limit]
    rows = []
    for idx, item in enumerate(items, 1):
        mk = f"{idx:02d}" if marker == "num" else marker
        rows.append(f'<div class="it"><span class="mk" style="color:{fg}">{esc(mk)}</span>'
                    f'<span>{esc(truncate(item, 220))}</span></div>')
    if not rows:
        rows.append(f'<div class="it" style="color:var(--ink-4)">{esc(empty)}</div>')
    return f'<div class="ds-list"><div class="h" style="color:{fg}">{esc(title)}</div>{"".join(rows)}</div>'


def callout(label: str, body: str, tone: str = "info") -> None:
    fg, bg = TONES.get(tone, TONES["info"])
    render(f'<div class="ds-call" style="background:{bg};border:1px solid color-mix(in srgb, {fg} 25%, transparent)">'
           f'<span class="lab" style="color:{fg}">{esc(label)}</span><div>{esc(body)}</div></div>')


def empty_state(message: str = "데이터 없음", hint: str = "") -> None:
    h = f'<div class="hint">{esc(hint)}</div>' if hint else ""
    render(f'<div class="ds-empty">{esc(message)}{h}</div>')


def sources_footer(tables: Iterable[tuple[str, str]]) -> None:
    rows = "".join(f"<div><b>{esc(t)}</b> · {esc(note)}</div>" for t, note in tables)
    render(f'<div class="ds-foot"><div style="letter-spacing:.08em;text-transform:uppercase;'
           f'color:var(--ink-3);margin-bottom:4px">Data sources · read-only</div>{rows}</div>')


def grid_html(cards: Iterable[str], cols: int = 3) -> str:
    return f'<div class="ds-grid c{cols}">' + "".join(cards) + "</div>"
