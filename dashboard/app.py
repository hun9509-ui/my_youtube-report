"""유튜브 전략 대시보드 — 단일 라우터 엔트리포인트.

실행: `streamlit run dashboard/app.py`
페이지는 dashboard/views/ 에 있으며 st.navigation 그룹으로 등록한다
(구식 pages/ 자동 등록을 쓰지 않는다).
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import streamlit as st  # noqa: E402

from components.ui import esc, inject_css, render  # noqa: E402
from lib.supabase_client import credentials_present, get_client  # noqa: E402

st.set_page_config(
    page_title="전략판 · 고은언니 한고은",
    page_icon=":material/insights:",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_css()


def _page(name: str, title: str, icon: str, default: bool = False):
    # url_path 는 파일명(stem)에서 유도 → /track_a_scores 등. (AppTest.switch_page 와도 일치)
    return st.Page(str(_HERE / "views" / f"{name}.py"), title=title, icon=icon, default=default)


PAGES = {
    "": [
        _page("home", "전략 브리핑", ":material/space_dashboard:", default=True),
    ],
    "Track A · 셀럽 유튜브": [
        _page("track_a_scores", "A1 기획 후보 큐", ":material/playlist_add_check:"),
        _page("track_a_celeb", "A2 경쟁 채널 · 히트 패턴", ":material/star:"),
        _page("track_a_own", "A3 고은언니 한고은", ":material/home:"),
        _page("track_a_weekly", "A4 주간 리포트 · 스냅샷", ":material/calendar_month:"),
    ],
    "Track B · 패션+유행": [
        _page("track_b_radar", "B1 트렌드 레이더", ":material/radar:"),
        _page("track_b_clusters", "B2 클러스터 · 미채택 기회", ":material/hub:"),
    ],
}

with st.sidebar:
    render(
        '<div class="ds-brand"><div class="logo">전</div><div>'
        '<div class="t">Strategy Intelligence</div>'
        f'<div class="s">{esc("고은언니 한고은 · 채널 전략판")}</div></div></div>'
    )

nav = st.navigation(PAGES, position="sidebar", expanded=True)

if not credentials_present():
    render('<div class="ds-head"><div><div class="ds-title">Supabase 연결 설정이 필요합니다</div>'
           '<div class="ds-lede">dashboard/.env.example 을 dashboard/.env 로 복사해 SUPABASE_URL / '
           'SUPABASE_KEY 를 채우거나, 같은 이름의 환경변수를 설정한 뒤 새로고침하세요.</div></div></div>')
    st.stop()

try:
    get_client()
except Exception as exc:  # 잘못된 URL/키
    st.error(f"Supabase 클라이언트 생성 실패: {type(exc).__name__}")
    st.stop()

with st.sidebar:
    st.caption("읽기 전용 · 5분 캐시")
    if st.button("데이터 새로고침", icon=":material/refresh:", width="stretch"):
        st.cache_data.clear()
        st.rerun()

nav.run()
