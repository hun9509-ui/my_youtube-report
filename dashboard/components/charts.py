"""Altair 차트 — 웜 오프화이트 톤에 맞춘 단일 테마.

모든 함수는 빈 데이터에서 빈 상태 컴포넌트를 그리고 조용히 반환한다.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from components.ui import empty_state

INK, INK2, INK3, INK4 = "#16140F", "#3D3A33", "#6C685E", "#9E9A8E"
LINE = "#E5E2D7"
TRACK_A, TRACK_B = "#B65A3C", "#2E7268"
UP, DOWN, INFO, WARN = "#3F8A5C", "#B8442F", "#5868A8", "#B9861D"
FONT = "Pretendard, -apple-system, system-ui, sans-serif"

STAGE_COLORS = {
    "breakout": DOWN, "rising": WARN, "mainstream": INFO,
    "early": "#A8A397", "seed": "#A8A397", "fatigue": "#CFCABD",
}
CATEGORICAL = [TRACK_A, TRACK_B, INFO, WARN, UP, "#8A6FA8", "#C0785A", "#5E8C9A", INK3, "#B7A16A"]


def _theme(chart: alt.Chart) -> alt.Chart:
    return (
        chart.configure(font=FONT, background="transparent")
        .configure_view(stroke=None)
        .configure_axis(labelColor=INK3, titleColor=INK3, labelFontSize=11, titleFontSize=11,
                        titleFontWeight=500, gridColor=LINE, gridDash=[2, 3], domainColor=LINE,
                        tickColor=LINE, labelPadding=6, titlePadding=10)
        .configure_legend(labelColor=INK2, titleColor=INK3, labelFontSize=11, titleFontSize=11,
                          orient="bottom", symbolSize=70, titleFontWeight=500)
        .configure_title(color=INK, fontSize=12, anchor="start")
    )


def _show(chart, height: int | None = None):
    if height:
        chart = chart.properties(height=height)
    st.altair_chart(_theme(chart), width="stretch")


def _empty(df) -> bool:
    if df is None or df.empty:
        empty_state("차트로 그릴 데이터가 없습니다.")
        return True
    return False


def bar_h(df: pd.DataFrame, x: str, y: str, *, color: str = INK, height: int | None = None,
          x_title: str = "", x_format: str | None = None, tooltip: list | None = None,
          rule: float | None = None, label_limit: int = 180, text_format: str | None = None):
    """정렬된 수평 막대 + 선택적 기준선."""
    if _empty(df):
        return
    height = height or max(120, 26 * len(df))
    axis = alt.Axis(format=x_format) if x_format else alt.Axis()
    scale = alt.Scale()
    if text_format:  # 막대 끝 라벨이 잘리지 않도록 여유 폭 확보
        top = pd.to_numeric(df[x], errors="coerce").max()
        if pd.notna(top) and top > 0:
            scale = alt.Scale(domain=[0, float(top) * 1.18], nice=False)
    base = alt.Chart(df).encode(
        y=alt.Y(f"{y}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=label_limit, labelColor=INK2)),
        x=alt.X(f"{x}:Q", title=x_title or None, axis=axis, scale=scale),
        tooltip=tooltip or [y, x],
    )
    layers = [base.mark_bar(color=color, cornerRadiusEnd=3, height={"band": 0.62})]
    if text_format:
        layers.append(base.mark_text(align="left", dx=4, color=INK3, fontSize=10.5)
                      .encode(text=alt.Text(f"{x}:Q", format=text_format)))
    if rule is not None:
        layers.append(alt.Chart(pd.DataFrame({"v": [rule]})).mark_rule(color=DOWN, strokeDash=[4, 3])
                      .encode(x="v:Q"))
    _show(alt.layer(*layers), height)


def lift_chart(df: pd.DataFrame, label: str = "value", *, baseline: float | None = None,
               color: str = TRACK_A, height: int | None = None):
    """히트율 막대 + 전체 히트율 기준선. df: value/n/hit_rate/median_ratio."""
    if _empty(df):
        return
    data = df.copy()
    data["표본"] = data["n"]
    tooltip = [alt.Tooltip(f"{label}:N", title="값"), alt.Tooltip("hit_rate:Q", title="히트율", format=".0%"),
               alt.Tooltip("표본:Q"), alt.Tooltip("median_ratio:Q", title="중앙 배수", format=".2f")]
    bar_h(data, "hit_rate", label, color=color, height=height, x_title="2.0배+ 히트율",
          x_format="%", tooltip=tooltip, rule=baseline, text_format=".0%")


def scatter_bubble(df: pd.DataFrame, *, x: str, y: str, size: str, color: str, tooltip: list,
                   x_title: str = "", y_title: str = "", color_domain: list | None = None,
                   color_range: list | None = None, height: int = 420, label: str | None = None,
                   x_rule: float | None = None):
    if _empty(df):
        return
    scale = alt.Scale(domain=color_domain, range=color_range) if color_domain else alt.Scale(range=CATEGORICAL)
    base = alt.Chart(df).encode(
        x=alt.X(f"{x}:Q", title=x_title or x, scale=alt.Scale(zero=False, nice=True, padding=18)),
        y=alt.Y(f"{y}:Q", title=y_title or y, scale=alt.Scale(zero=False, nice=True, padding=18)),
    )
    points = base.mark_circle(opacity=0.72, stroke="white", strokeWidth=0.8).encode(
        size=alt.Size(f"{size}:Q", legend=None, scale=alt.Scale(range=[30, 900])),
        color=alt.Color(f"{color}:N", scale=scale, title=None),
        tooltip=tooltip,
    )
    layers = [points]
    if x_rule is not None:
        layers.append(alt.Chart(pd.DataFrame({"v": [x_rule]})).mark_rule(color=INK4, strokeDash=[4, 3])
                      .encode(x="v:Q"))
    if label:
        layers.append(base.mark_text(align="left", dx=7, dy=-6, fontSize=10.5, color=INK2)
                      .encode(text=f"{label}:N")
                      .transform_filter(alt.datum._label == True))  # noqa: E712
    _show(alt.layer(*layers).interactive(), height)


def lines(df: pd.DataFrame, *, x: str, y: str, color: str | None = None, x_title: str = "",
          y_title: str = "", height: int = 280, x_type: str = "Q", highlight: str | None = None,
          single_color: str = TRACK_A, y_format: str | None = None, points: bool = True):
    if _empty(df):
        return
    enc = dict(
        x=alt.X(f"{x}:{x_type}", title=x_title or None),
        y=alt.Y(f"{y}:Q", title=y_title or None, axis=alt.Axis(format=y_format) if y_format else alt.Axis()),
        tooltip=[c for c in df.columns if not str(c).startswith("_")],
    )
    if color:
        enc["color"] = alt.Color(f"{color}:N", title=None, scale=alt.Scale(range=CATEGORICAL),
                                 legend=alt.Legend(columns=3, labelLimit=220))
        if highlight is not None:
            enc["opacity"] = alt.condition(alt.datum[color] == highlight, alt.value(1), alt.value(0.28))
            enc["strokeWidth"] = alt.condition(alt.datum[color] == highlight, alt.value(3), alt.value(1.4))
    mark = alt.Chart(df).mark_line(point=points, strokeWidth=2, interpolate="monotone")
    if not color:
        mark = alt.Chart(df).mark_line(point=points, strokeWidth=2.2, color=single_color, interpolate="monotone")
    _show(mark.encode(**enc), height)


def stacked_bar(df: pd.DataFrame, *, x: str, y: str, color: str, height: int | None = None,
                color_domain: list | None = None, color_range: list | None = None, x_title: str = "",
                normalize: bool = False):
    if _empty(df):
        return
    scale = alt.Scale(domain=color_domain, range=color_range) if color_domain else alt.Scale(range=CATEGORICAL)
    chart = alt.Chart(df).mark_bar(height={"band": 0.65}).encode(
        y=alt.Y(f"{y}:N", title=None, sort="-x", axis=alt.Axis(labelLimit=180, labelColor=INK2)),
        x=alt.X(f"sum({x}):Q", title=x_title or None, stack="normalize" if normalize else "zero",
                axis=alt.Axis(format="%") if normalize else alt.Axis()),
        color=alt.Color(f"{color}:N", scale=scale, title=None),
        tooltip=[y, color, x],
    )
    _show(chart, height or max(140, 28 * df[y].nunique()))


def metric_heatmap(df: pd.DataFrame, *, row: str, col: str, value: str, height: int | None = None,
                   color: str = TRACK_B, col_sort: list | None = None):
    """엔티티 × 지표 히트맵 (값 0~1)."""
    if _empty(df):
        return
    base = alt.Chart(df).encode(
        x=alt.X(f"{col}:N", title=None, sort=col_sort, axis=alt.Axis(labelAngle=0, orient="top", labelColor=INK2)),
        y=alt.Y(f"{row}:N", title=None, sort=None, axis=alt.Axis(labelLimit=200, labelColor=INK2)),
    )
    rect = base.mark_rect(cornerRadius=3, stroke="white", strokeWidth=2).encode(
        color=alt.Color(f"{value}:Q", scale=alt.Scale(domain=[0, 1], range=["#F2F1EB", color]), legend=None),
        tooltip=[row, col, alt.Tooltip(f"{value}:Q", format=".2f")],
    )
    text = base.mark_text(fontSize=10.5, font="JetBrains Mono").encode(
        text=alt.Text(f"{value}:Q", format=".2f"),
        color=alt.condition(alt.datum[value] > 0.55, alt.value("white"), alt.value(INK2)),
    )
    _show(alt.layer(rect, text), height or max(160, 30 * df[row].nunique()))
