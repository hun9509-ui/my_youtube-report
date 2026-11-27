"""B1 · 패션·유행 트렌드 레이더 — trend_entity_scores 최신 스냅샷 + trend_answer_cards."""

import streamlit as st

from components.cards import entity_card_html
from components.charts import STAGE_COLORS, metric_heatmap, scatter_bubble, stacked_bar
from components.ui import (
    empty_state,
    esc,
    kpi_strip,
    link_html,
    list_block_html,
    page_header,
    pill_html,
    render,
    section,
    sources_footer,
)
from lib.constants import B_METRICS, CATEGORY_LABELS, FORMAT_LABELS, STAGE_META, STAGE_ORDER
from lib.queries import get_b_track_answer_cards, get_b_track_entity_scores
from lib.supabase_client import get_client
from lib.transforms import (
    b_action_rank,
    fmt_date,
    fmt_int,
    fmt_ratio,
    fmt_views,
    normalize_list,
    parse_jsonish,
    stage_rank,
    truncate,
)

client = get_client()
ents = get_b_track_entity_scores(client)
snapshot = ents["scored_at"].iloc[0] if not ents.empty else None

page_header(
    "패션·유행 트렌드 레이더",
    "407개 패션·뷰티·라이프 채널에서 감지된 아이템·브랜드·스타일 엔티티를 단계·포맷·커머스 지표로 거릅니다.",
    track="B", code="B1",
    meta=[pill_html(f"스냅샷 {fmt_date(snapshot, '%Y.%m.%d')}", "up" if snapshot else "muted")],
)
if ents.empty:
    empty_state("trend_entity_scores 최신 스냅샷이 없습니다.", "b_track_scorer 실행 후 확인하세요.")
    st.stop()

stage_label = lambda s: STAGE_META.get(s, (s or "—", ""))[0]  # noqa: E731
present_stages = sorted(ents["trend_stage"].dropna().unique().tolist(), key=stage_rank)
present_cats = sorted(ents["category"].dropna().unique().tolist())
present_fmts = [f for f in FORMAT_LABELS if f in set(ents["best_format"].dropna())]
present_actions = sorted(ents["recommended_action"].dropna().unique().tolist(), key=b_action_rank)

# ── 필터 ──────────────────────────────────────────────────
f1, f2 = st.columns([1.4, 1])
with f1:
    stages = st.pills("트렌드 단계", present_stages, selection_mode="multi",
                      default=[s for s in present_stages if s in ("breakout", "rising")] or present_stages,
                      format_func=lambda s: f"{stage_label(s)} · {s}", key="b1_stage")
with f2:
    actions = st.pills("실행 등급", present_actions, selection_mode="multi", default=present_actions, key="b1_action")
g1, g2, g3, g4 = st.columns([1.2, 1.2, 1, 0.8])
with g1:
    cats = st.multiselect("카테고리", present_cats, format_func=lambda c: CATEGORY_LABELS.get(c, c),
                          placeholder="전체", key="b1_cat")
with g2:
    fmts = st.multiselect("주력 포맷", present_fmts, format_func=lambda f: FORMAT_LABELS.get(f, f),
                          placeholder="8종 전체", key="b1_fmt")
with g3:
    kw = st.text_input("키워드", placeholder="엔티티명 검색", key="b1_kw")
with g4:
    min_ch = st.number_input("최소 채널(30일)", 0, 50, 2, key="b1_minch")

df = ents.copy()
if stages:
    df = df[df["trend_stage"].isin(stages)]
if actions:
    df = df[df["recommended_action"].isin(actions)]
if cats:
    df = df[df["category"].isin(cats)]
if fmts:
    df = df[df["best_format"].isin(fmts)]
if kw:
    df = df[df["entity_name"].fillna("").str.contains(kw, case=False, regex=False)]
df = df[df["distinct_channels_30d"].fillna(0) >= min_ch]
df = df.assign(_s=df["trend_stage"].map(stage_rank), _a=df["recommended_action"].map(b_action_rank)) \
    .sort_values(["_a", "_s", "median_view_over_baseline"], ascending=[True, True, False]).drop(columns=["_s", "_a"])

stage_counts = ents["trend_stage"].value_counts()
kpi_strip([
    {"label": "엔티티 (필터)", "value": fmt_int(len(df)), "sub": f"전체 {fmt_int(len(ents))}"},
    *[{"label": f"{stage_label(s)}", "value": fmt_int(stage_counts.get(s, 0)), "sub": s,
       "tone": STAGE_META.get(s, ("", "neutral"))[1]} for s in present_stages[:4]],
    {"label": "바로 제작 · 기획 후보", "value": fmt_int(ents["recommended_action"].isin(["바로 제작", "기획 후보"]).sum()),
     "tone": "B"},
])
if df.empty:
    empty_state("필터 조건에 맞는 엔티티가 없습니다.", "단계·최소 채널 조건을 완화해 보세요.")
    st.stop()

# ── 레이더 차트 ──────────────────────────────────────────
section("확산 × 성과 버블", "x = 채널 기준선 대비 중앙 배수 · y = 30일 언급 채널 수 · 크기 = 30일 언급량", kicker="01")
plot = df.copy()
plot["단계"] = plot["trend_stage"]
plot["포맷"] = plot["best_format"].map(lambda f: FORMAT_LABELS.get(f, f))
top_names = set(plot.nlargest(10, "distinct_channels_30d")["entity_name"]) | \
    set(plot.nlargest(6, "median_view_over_baseline")["entity_name"])
plot["_label"] = plot["entity_name"].isin(top_names)
domain = [s for s in STAGE_ORDER if s in set(plot["단계"])]
scatter_bubble(
    plot, x="median_view_over_baseline", y="distinct_channels_30d", size="mention_count_30d", color="단계",
    x_title="중앙 view_over_baseline (배)", y_title="30일 언급 채널 수",
    color_domain=domain, color_range=[STAGE_COLORS.get(s, "#A8A397") for s in domain], label="entity_name",
    x_rule=1.0,
    tooltip=["entity_name", "category", "단계", "포맷", "recommended_action", "distinct_channels_30d",
             "mention_count_30d", "median_view_over_baseline", "commerce_signal", "shopping_intent_score"],
)

c1, c2 = st.columns([1, 1.2])
with c1:
    section("단계 × 주력 포맷", "필터된 엔티티 분포", kicker="02")
    mix = plot.groupby(["포맷", "단계"]).size().reset_index(name="n")
    stacked_bar(mix, x="n", y="포맷", color="단계", color_domain=domain,
                color_range=[STAGE_COLORS.get(s, "#A8A397") for s in domain], x_title="엔티티 수")
with c2:
    section("커머스 지표 비교", "상위 12개 엔티티 · 0~1 정규화", kicker="03")
    top = df.head(12)
    heat = top.melt(id_vars=["entity_name"], value_vars=[k for k, _ in B_METRICS], var_name="m", value_name="v")
    heat["지표"] = heat["m"].map(dict(B_METRICS))
    heat["엔티티"] = heat["entity_name"].map(lambda n: truncate(n, 18))
    metric_heatmap(heat.dropna(subset=["v"]), row="엔티티", col="지표", value="v",
                   col_sort=[label for _, label in B_METRICS])

# ── 전체 테이블 ──────────────────────────────────────────
section("엔티티 지표 테이블", "B-track v2 패션·커머스 전체 지표", kicker="04", right=f"{len(df)}행")
table = df.copy()
table["trend_stage"] = table["trend_stage"].map(lambda s: f"{stage_label(s)} · {s}")
table["best_format"] = table["best_format"].map(lambda f: FORMAT_LABELS.get(f, f))
table["category"] = table["category"].map(lambda c: CATEGORY_LABELS.get(c, c))
cols = ["entity_name", "category", "trend_stage", "recommended_action", "best_format", "distinct_channels_30d",
        "mention_count_30d", "median_view_over_baseline", *[k for k, _ in B_METRICS], "suggested_angle"]
prog = lambda label: st.column_config.ProgressColumn(label, min_value=0, max_value=1, format="%.2f")  # noqa: E731
st.dataframe(
    table[[c for c in cols if c in table.columns]], hide_index=True, height=460,
    column_config={
        "entity_name": st.column_config.TextColumn("엔티티", pinned=True),
        "category": "카테고리", "trend_stage": "단계", "recommended_action": "실행", "best_format": "포맷",
        "distinct_channels_30d": st.column_config.NumberColumn("채널30d", format="%d"),
        "mention_count_30d": st.column_config.NumberColumn("언급30d", format="%d"),
        "median_view_over_baseline": st.column_config.NumberColumn("중앙 배수", format="%.2f×"),
        **{k: prog(label) for k, label in B_METRICS},
        "suggested_angle": st.column_config.TextColumn("제안 앵글", width="large"),
    },
)

# ── 답변 카드 ────────────────────────────────────────────
section("엔티티 브리핑 카드", "trend_answer_cards 최신 card_date 결합 · 펼쳐서 근거·주의·아이디어 확인", kicker="05")
cards = get_b_track_answer_cards(client)
card_map = {r["entity_id"]: r for r in cards.to_dict("records")} if not cards.empty else {}
n_cards = st.slider("카드 수", 3, 30, 9, step=3, key="b1_ncards")
shown = df.head(n_cards)
for start in range(0, len(shown), 3):
    cols3 = st.columns(3)
    for col, (_, row) in zip(cols3, list(shown.iloc[start:start + 3].iterrows())):
        with col:
            render(entity_card_html(row))
            card = card_map.get(row.get("entity_id"))
            with st.expander("브리핑 · 근거 · 아이디어", icon=":material/description:"):
                if not card:
                    st.caption("이 엔티티의 최신 답변 카드가 없습니다.")
                else:
                    st.markdown(card.get("answer_summary") or "")
                    render(list_block_html("근거", normalize_list(card.get("evidence_bullets")), "B", "·"))
                    render('<div style="height:6px"></div>')
                    render(list_block_html("주의", normalize_list(card.get("caution_bullets")), "warn", "!"))
                    render('<div style="height:6px"></div>')
                    render(list_block_html("콘텐츠 아이디어", normalize_list(card.get("recommended_content_ideas")), "up"))
                examples = parse_jsonish((card or {}).get("best_examples")) or parse_jsonish(row.get("best_examples")) or []
                if isinstance(examples, list) and examples:
                    st.markdown("**베스트 예시**")
                    for ex in examples[:3]:
                        if isinstance(ex, dict):
                            render(f'<div class="ds-csub" style="padding:3px 0">'
                                   f'{link_html(ex.get("video_url"), ex.get("title"), 60)} · '
                                   f'{esc(ex.get("channel_title") or "")} · {fmt_ratio(ex.get("view_over_baseline"))} · '
                                   f'{fmt_views(ex.get("view_count"))}</div>')

sources_footer([
    ("trend_entity_scores", f"scored_at={snapshot} 단일 스냅샷 · trend_stage, best_format, engagement/commerce/item_heat/shopping_intent/ppl/channel_fit/difficulty/originality"),
    ("trend_answer_cards", "최신 card_date · answer_summary, evidence/caution_bullets, recommended_content_ideas, best_examples"),
])
