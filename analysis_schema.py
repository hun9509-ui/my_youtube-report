"""모델 응답을 DB 저장과 텔레그램 출력 전에 검증한다."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class VideoAnalysis(AnalysisOutput):
    topic: RequiredText
    category: str = ""
    title_pattern: str = ""
    title_keywords: list[str] = Field(default_factory=list)
    hook_strategy: str = ""
    ppl_likely: bool = False
    ppl_signals: str = ""
    target_audience: str = ""
    performance_level: str = ""
    success_factors: str = ""
    applicability: str = ""
    applicability_reason: str = ""


class CommentAnalysis(AnalysisOutput):
    summary: RequiredText
    sentiment: dict[str, int] = Field(default_factory=dict)
    main_keywords: list[str] = Field(default_factory=list)
    viewer_persona: str = ""
    praise_points: list[str] = Field(default_factory=list)
    complaints: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    ad_boost_suspected: bool = False
    ad_boost_score: int = Field(default=0, ge=0, le=100)


class WeeklyReport(AnalysisOutput):
    week_summary: RequiredText
    top_performing_channels: list[str] = Field(default_factory=list)
    rising_topics: list[str] = Field(default_factory=list)
    format_trends: list[str] = Field(default_factory=list)
    ppl_observations: str = ""
    actionable_insights_for_hangoeun: list[str] = Field(default_factory=list)
    next_video_suggestions: list[str] = Field(default_factory=list)


class MarketAnalysis(AnalysisOutput):
    market_summary: RequiredText
    dominant_emotion: str = ""
    viewer_fatigue_signals: list[str] = Field(default_factory=list)
    hangoeun_recommended_topics: list[str] = Field(default_factory=list)
    competitive_gap: list[str] = Field(default_factory=list)
    comfort_content_score: int = Field(default=0, ge=0, le=100)
    hangoeun_opportunity_score: int = Field(default=0, ge=0, le=100)
