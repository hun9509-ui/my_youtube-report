"""
B-track YouTube API 유닛 예산 가드
절대 제약: A-track 몫을 침범하지 않는다. 예산 소진 시 즉시 중단·익일 이월.

YouTube Data API 할당량은 Pacific Time 자정에 리셋되므로, 일일 카운터도 PT 기준으로 키를 잡는다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import b_track_config as btc
from supabase import create_client

_PT = ZoneInfo("America/Los_Angeles")


def _today_pt() -> str:
    return datetime.now(_PT).date().isoformat()


class QuotaExceeded(Exception):
    """예산 초과 — 호출 중단 신호"""
    pass


class QuotaGuard:
    """일일 유닛 예산 추적기. spend 전 can_spend로 확인, 초과 시 hard stop."""

    def __init__(self):
        self.db = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        self.budget = btc.B_TRACK_DAILY_QUOTA_BUDGET
        self.read_reserve = btc.B_TRACK_READ_RESERVE
        self.cost = btc.QUOTA_COST
        self.date = _today_pt()
        self._load()

    def _load(self):
        row = self.db.table('b_track_quota_usage').select('*').eq(
            'usage_date', self.date
        ).execute()
        if row.data:
            r = row.data[0]
            self.used = r.get('units_used', 0)
            self.search_calls = r.get('search_calls', 0)
            self.read_calls = r.get('read_calls', 0)
        else:
            self.used = 0
            self.search_calls = 0
            self.read_calls = 0
            self.db.table('b_track_quota_usage').insert({
                'usage_date': self.date, 'units_used': 0,
                'search_calls': 0, 'read_calls': 0,
            }).execute()

    def remaining(self) -> int:
        return self.budget - self.used

    def search_remaining(self) -> int:
        """search에 쓸 수 있는 유닛 = 전체 잔여 − 읽기 예비분"""
        return max(0, self.remaining() - self.read_reserve)

    def can_spend(self, op: str, n: int = 1) -> bool:
        need = self.cost[op] * n
        if op == 'search':
            return self.search_remaining() >= need
        return self.remaining() >= need

    def spend(self, op: str, n: int = 1):
        """유닛 차감 + 즉시 영속화. 예산 초과면 QuotaExceeded."""
        need = self.cost[op] * n
        if not self.can_spend(op, n):
            raise QuotaExceeded(
                f"예산 초과: op={op} need={need} "
                f"used={self.used}/{self.budget} (search여유={self.search_remaining()})"
            )
        self.used += need
        if op == 'search':
            self.search_calls += n
        else:
            self.read_calls += n
        self.db.table('b_track_quota_usage').update({
            'units_used': self.used,
            'search_calls': self.search_calls,
            'read_calls': self.read_calls,
            'updated_at': datetime.now(_PT).isoformat(),
        }).eq('usage_date', self.date).execute()

    def report(self) -> str:
        return (
            f"[유닛] {self.used}/{self.budget} 사용 "
            f"(search {self.search_calls}회, read {self.read_calls}회, "
            f"search여유 {self.search_remaining()}, PT {self.date})"
        )
