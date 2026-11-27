"""Supabase 읽기 전용 클라이언트.

키는 환경변수 또는 .env 에서만 읽는다(하드코딩 금지).
탐색 순서: 이미 설정된 환경변수 → dashboard/.env → 저장소 루트 .env
"""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

_DASHBOARD_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_DASHBOARD_DIR / ".env", override=False)
load_dotenv(_DASHBOARD_DIR.parent / ".env", override=False)


def credentials_present() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


@st.cache_resource(show_spinner=False)
def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY 환경변수가 없습니다.")
    from supabase import create_client

    return create_client(url, key)
