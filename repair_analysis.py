"""분석 데이터 수리 및 백필. 기본 실행은 읽기 전용이다."""
import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config
import deepseek_processor as ds
import gemini_analyzer as gemini
import strategy_scorer
import supabase_writer as db
import youtube_collector as yt
from analysis_schema import CommentAnalysis, OwnVideoAnalysis, VideoAnalysis
from gemini_client import has_text, safe_error


STEPS = (
    "daily-cleanup", "daily-reanalyze", "scores-recompute",
    "own-reanalyze", "weekly-cleanup", "dead-sources",
)
COMMENT_DEFAULTS = {
    "sentiment": {}, "main_keywords": [], "viewer_persona": "",
    "praise_points": [], "complaints": [], "suggestions": [],
    "comments_summary": "", "ad_boost_suspected": False, "ad_boost_score": 0,
}
SCORE_FIELDS = (
    "priority_score", "hangoeun_fit_score", "execution_score",
    "repeatability_score", "novelty_score", "risk_score",
    "ppl_potential_score", "recommended_action", "source_table",
)


def _is_valid_gemini(row):
    topic = row.get("topic")
    return has_text(row.get("gemini_raw"), "topic") and isinstance(topic, str) and bool(topic.strip())


def _is_valid_deepseek(raw):
    return isinstance(raw, dict) and "error" not in raw and (
        has_text(raw, "summary") or bool(raw.get("sentiment")) or bool(raw.get("praise_points"))
    )


def _comment_fields(raw):
    """Flatten the same comment fields as the normal daily writer."""
    fields = {column: raw.get(column, default) for column, default in COMMENT_DEFAULTS.items()}
    fields["comments_summary"] = raw.get("summary", "")
    fields["deepseek_raw"] = raw
    return fields


def _preserved_comments(row):
    fields = _comment_fields(row["deepseek_raw"])
    fields.update({key: row[key] for key in COMMENT_DEFAULTS if key in row})
    return fields


def _latest(rows, valid):
    # Callers read in ascending id order, so the last valid row wins.
    return {row["video_id"]: row for row in rows if valid(row)}


def _models_result(raw, schema, required):
    if isinstance(raw, dict) and "error" in raw:
        raise ValueError(safe_error(raw["error"]))
    validated = schema.model_validate(raw).model_dump()
    if not has_text(validated, required):
        raise ValueError(f"유효한 {required} 없음")
    return validated


def _in_batches(items):
    for start in range(0, len(items), 100):
        yield items[start:start + 100]


def _published_at(video):
    value = video.get("published_at")
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    timestamp = datetime.fromisoformat(value)
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


def _not_found(exc):
    if exc.resp.status != 404:
        return False
    try:
        errors = json.loads(exc.content)["error"]["errors"]
        return any(item["reason"] in {"playlistNotFound", "channelNotFound"} for item in errors)
    except (ValueError, TypeError, KeyError):
        return False


class Repair:
    def __init__(self, client, args, youtube=None):
        self.client = client
        self.args = args
        self.youtube = youtube
        self.preserved_deepseek = {}
        self.backup_dir = args.backup_dir or (
            Path("backups") / datetime.now(config.KST).strftime("repair-%Y%m%d-%H%M%S")
        )
        self.processing_key = False
        self.report = {"apply": args.apply, "steps": {}}

    def _rows(self, table, order="id", **filters):
        def query():
            result = self.client.table(table).select("*").order(order)
            for column, value in filters.items():
                result = result.eq(column, value)
            return result

        return list(db._read_pages(query))

    def _backup(self, filename, rows):
        self.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.backup_dir / filename
        if self.args.json_report and self.args.json_report.resolve() == path.resolve():
            raise ValueError("--json-report와 백업 파일 경로가 같습니다")
        with open(path, "x", encoding="utf-8", opener=_private_file) as output:
            json.dump(rows, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())

    def _delete(self, table, rows):
        for chunk in _in_batches([row["id"] for row in rows]):
            self.client.table(table).delete().in_("id", chunk).execute()

    def _update(self, table, key, value, fields):
        self.client.table(table).update(fields).eq(key, value).execute()

    def _failure(self, stats, exc):
        stats["failed"] += 1
        print(safe_error(f"수리 실패: {safe_error(exc)}"), file=sys.stderr)

    def _has_processing_key(self):
        try:
            self.client.table("daily_analysis").select("processing_key").limit(1).execute()
        except Exception as exc:
            print(safe_error(f"processing_key 확인 실패: {safe_error(exc)}"), file=sys.stderr)
            return False
        return True

    def run(self):
        if set(self.args.steps) & {"daily-cleanup", "daily-reanalyze"}:
            self.processing_key = self._has_processing_key()
            self.report["processing_key"] = self.processing_key
            if (self.args.apply and "daily-reanalyze" in self.args.steps
                    and not (self.processing_key or self.args.allow_without_migration)):
                raise RuntimeError("daily_analysis.processing_key 없음. db_migrate.py를 먼저 실행하세요.")

        prepare = {
            "daily-cleanup": self.daily_cleanup,
            "daily-reanalyze": self.daily_reanalyze,
            "scores-recompute": self.scores_recompute,
            "own-reanalyze": self.own_reanalyze,
            "weekly-cleanup": self.weekly_cleanup,
            "dead-sources": self.dead_sources,
        }
        # Finish every stage's backup before making the first database mutation.
        actions = [prepare[step]() for step in STEPS if step in self.args.steps]
        if self.args.apply:
            for action in actions:
                action()
        return not any(stats.get("failed") or stats.get("comment_failures")
                       for stats in self.report["steps"].values())

    def daily_cleanup(self):
        groups = defaultdict(list)
        for row in self._rows("daily_analysis"):
            groups[row["video_id"]].append(row)

        deleted, merges, keys = [], [], []
        survivors = 0
        for video_id, rows in groups.items():
            survivor = next((row for row in reversed(rows) if _is_valid_gemini(row)), None)
            discarded = [row for row in rows if row is not survivor]
            deleted.extend(discarded)
            donor = next((row for row in reversed(discarded)
                          if _is_valid_deepseek(row.get("deepseek_raw"))), None)
            if survivor is None:
                if donor:
                    self.preserved_deepseek[video_id] = _preserved_comments(donor)
                continue
            survivors += 1
            if donor and not _is_valid_deepseek(survivor.get("deepseek_raw")):
                merges.append((survivor, _preserved_comments(donor)))
            if self.processing_key and not survivor.get("processing_key"):
                keys.append(survivor)

        self.report["steps"]["daily-cleanup"] = {
            "survivors": survivors, "delete_candidates": len(deleted),
            "comment_merges": len(merges), "processing_key_backfills": len(keys),
            "comments_preserved": len(self.preserved_deepseek),
        }
        if self.preserved_deepseek and "daily-reanalyze" not in self.args.steps:
            msg = (
                f"daily-cleanup 단독 실행으로 유효 댓글 분석이 포함된 실패 전용 영상 "
                f"{len(self.preserved_deepseek)}건이 삭제 후보에 포함되어 메모리/백업 JSON에만 보존됩니다."
            )
            self.report.setdefault("warnings", []).append(msg)
            print(f"⚠️ {msg}", file=sys.stderr)
        if self.args.apply and (deleted or merges or keys):
            originals = {row["id"]: row for row in deleted + keys + [row for row, _ in merges]}
            self._backup("daily_cleanup_backup.json", list(originals.values()))

        def apply():
            for row, fields in merges:
                self._update("daily_analysis", "id", row["id"], fields)
            self._delete("daily_analysis", deleted)
            # A discarded row can already own this unique processing key.
            for row in keys:
                self._update("daily_analysis", "id", row["id"], {"processing_key": row["video_id"]})

        return apply

    def _analysis_rows(self):
        initial = _latest(self._rows("initial_analysis"), lambda row: has_text(row.get("gemini_raw"), "topic"))
        return initial, self._rows("daily_analysis")

    def _daily_targets(self, videos, initial, daily_rows):
        daily = _latest(daily_rows, _is_valid_gemini)
        candidates = [video for video_id, video in sorted(videos.items())
                      if video_id not in initial and video_id not in daily]
        candidates.sort(key=_published_at, reverse=True)
        comment_errors = [row for row in daily_rows if _is_valid_gemini(row)
                          and isinstance(row.get("deepseek_raw"), dict) and "error" in row["deepseek_raw"]]
        return candidates[:self.args.limit], comment_errors

    def _comments(self, video):
        if (video.get("comment_count") or 0) <= 0:
            return {"skipped": "댓글 없음"}
        if not self.args.with_comments:
            return {"skipped": "repair_gemini_only"}
        comments = yt.get_video_comments(
            video["video_id"], max_comments=config.TOP_COMMENTS_COUNT, raise_on_error=True
        )
        if not comments:
            return {"skipped": "댓글 수집 결과 없음"}
        return _models_result(ds.analyze_comments(video.get("title") or "", comments), CommentAnalysis, "summary")

    def _save_daily(self, video_id, result, comments, preserved):
        if self.processing_key:
            entry = {"video_id": video_id, "gemini": result, "comments": comments}
            if db.save_daily_analysis([entry]) != 1:
                raise RuntimeError("일일 분석 저장 건수가 다릅니다")
            if preserved and preserved != _comment_fields({**comments, "_version": config.ANALYSIS_VERSION}):
                self._update("daily_analysis", "processing_key", video_id, preserved)
            return True

        # Legacy schemas have no unique processing key. Re-read immediately before insert.
        if any(_is_valid_gemini(row) for row in self._rows("daily_analysis", video_id=video_id)):
            return False
        payload = {
            "video_id": video_id,
            "analysis_date": datetime.now(config.KST).date().isoformat(),
            **{key: result[key] for key in VideoAnalysis.model_fields},
            "gemini_raw": {**result, "_version": config.ANALYSIS_VERSION},
            **(preserved or _comment_fields({**comments, "_version": config.ANALYSIS_VERSION})),
        }
        self.client.table("daily_analysis").insert(payload).execute()
        return True

    def daily_reanalyze(self):
        videos = {row["video_id"]: row for row in self._rows("videos", "video_id")}
        initial, daily_rows = self._analysis_rows()
        candidates, comment_errors = self._daily_targets(videos, initial, daily_rows)
        stats = self.report["steps"]["daily-reanalyze"] = {
            "candidates": len(candidates), "comment_errors": len(comment_errors),
            "saved": 0, "comments_recovered": 0, "already_analyzed": 0,
            "comment_failures": 0, "failed": 0,
        }
        if self.args.apply and (candidates or (self.args.with_comments and comment_errors)):
            candidate_ids = {video["video_id"] for video in candidates}
            comment_ids = {row["id"] for row in comment_errors} if self.args.with_comments else set()
            originals = [row for row in daily_rows if row.get("processing_key") in candidate_ids
                         or row["video_id"] in candidate_ids or row["id"] in comment_ids]
            self._backup("daily_reanalyze_backup.json", originals)

        def apply():
            errors_by_video = defaultdict(list)
            if self.args.with_comments:
                current_errors = comment_errors
                if "daily-cleanup" in self.args.steps:
                    _, current_errors = self._daily_targets(videos, initial, self._rows("daily_analysis"))
                stats["comment_errors"] = len(current_errors)
                for row in current_errors:
                    errors_by_video[row["video_id"]].append(row)

            work = [(video["video_id"], video, []) for video in candidates]
            work.extend((vid, videos.get(vid), rows) for vid, rows in errors_by_video.items())
            for index, (video_id, video, errors) in enumerate(work):
                if index and self.args.delay > 0:
                    time.sleep(self.args.delay)
                try:
                    if video is None:
                        raise ValueError("댓글 복구에 필요한 영상 메타데이터 없음")
                    if errors:
                        comments = self._comments(video)
                        fields = _comment_fields({**comments, "_version": config.ANALYSIS_VERSION})
                        for row in errors:
                            self._update("daily_analysis", "id", row["id"], fields)
                            stats["comments_recovered"] += 1
                        continue

                    result = _models_result(gemini.analyze_video(video, model_mode="daily"), VideoAnalysis, "topic")
                    preserved = self.preserved_deepseek.get(video_id)
                    if preserved:
                        comments = preserved["deepseek_raw"]
                    else:
                        try:
                            comments = self._comments(video)
                        except Exception as exc:
                            stats["comment_failures"] += 1
                            error = safe_error(exc)
                            print(safe_error(f"댓글 분석 실패: {error}"), file=sys.stderr)
                            comments = {"error": error}
                    saved = self._save_daily(video_id, result, comments, preserved)
                    stats["saved" if saved else "already_analyzed"] += 1
                except Exception as exc:
                    self._failure(stats, exc)

        return apply

    def _score_plan(self, stats, videos, scores):
        initial, daily_rows = self._analysis_rows()
        daily = _latest(daily_rows, _is_valid_gemini)
        existing = {row["video_id"]: row for row in scores}
        changed = []
        for video_id in sorted(initial.keys() | daily.keys()):
            analysis, source = (
                (initial[video_id], "initial_analysis") if video_id in initial
                else (daily[video_id], "daily_analysis")
            )
            if not isinstance(analysis.get("deepseek_raw"), dict):
                analysis = {**analysis, "deepseek_raw": {}}
            try:
                score = strategy_scorer.score_video(videos.get(video_id, {}), analysis, source)
                old = existing.get(video_id)
                if old is None or any(old.get(field) != score.get(field) for field in SCORE_FIELDS):
                    changed.append(score)
            except Exception as exc:
                self._failure(stats, exc)
        orphans = [row for row in scores if row["video_id"] not in initial and row["video_id"] not in daily]
        stats.update(upsert_candidates=len(changed), orphan_scores=len(orphans))
        return changed, orphans

    def scores_recompute(self):
        videos = {row["video_id"]: row for row in self._rows("videos", "video_id")}
        scores = self._rows("video_scores", score_version=config.SCORE_VERSION)
        stats = self.report["steps"]["scores-recompute"] = {"saved": 0, "orphans_deleted": 0, "failed": 0}
        daily_changes_first = self.args.apply and bool(
            set(self.args.steps) & {"daily-cleanup", "daily-reanalyze"}
        )
        if daily_changes_first:
            # Any current score may need to change once the daily analysis is repaired.
            originals = scores
            possible_orphans = scores
        else:
            changed, orphans = self._score_plan(stats, videos, scores)
            changed_ids = {row["video_id"] for row in changed}
            originals = [row for row in scores if row["video_id"] in changed_ids]
            possible_orphans = orphans
        if self.args.apply:
            if daily_changes_first or changed:
                self._backup("scores_recompute_backup.json", originals)
            if self.args.delete_orphan_scores and possible_orphans:
                self._backup("orphan_scores_backup.json", possible_orphans)

        def apply():
            upserts, orphan_rows = self._score_plan(stats, videos, scores) if daily_changes_first else (changed, orphans)
            for chunk in _in_batches(upserts):
                saved = db.save_video_scores(chunk)
                stats["saved"] += saved
                if saved != len(chunk):
                    raise RuntimeError("전략 점수 저장 건수가 다릅니다")
            if self.args.delete_orphan_scores:
                self._delete("video_scores", orphan_rows)
                stats["orphans_deleted"] = len(orphan_rows)

        return apply

    def own_reanalyze(self):
        rows = self._rows("own_channel_analysis", "video_id")
        videos = {row["video_id"]: row for row in self._rows("own_channel_videos", "video_id")}
        targets = [row for row in rows if not _is_valid_gemini(row)][:self.args.limit]
        stats = self.report["steps"]["own-reanalyze"] = {"candidates": len(targets), "saved": 0, "failed": 0}
        if self.args.apply and targets:
            self._backup("own_reanalyze_backup.json", targets)

        def apply():
            for index, row in enumerate(targets):
                if index and self.args.delay > 0:
                    time.sleep(self.args.delay)
                video_id = row["video_id"]
                try:
                    video = videos.get(video_id)
                    if video is None:
                        raise ValueError("자체 채널 영상 메타데이터 없음")
                    result = _models_result(gemini.analyze_own_video(video), OwnVideoAnalysis, "topic")
                    raw = row.get("deepseek_raw")
                    preserved = raw if isinstance(raw, dict) else {}
                    if not db.save_own_analysis(video_id, result, preserved):
                        raise RuntimeError("자체 채널 분석 저장 실패")
                    if isinstance(raw, dict) and "error" not in raw and raw.get("_version") != config.ANALYSIS_VERSION:
                        # The normal writer stamps its current version even when accepting existing comments.
                        self._update("own_channel_analysis", "video_id", video_id, {"deepseek_raw": raw})
                    stats["saved"] += 1
                except Exception as exc:
                    self._failure(stats, exc)

        return apply

    def weekly_cleanup(self):
        rows = self._rows("weekly_reports")
        latest = {row["report_date"]: row["id"] for row in rows if (row.get("week_summary") or "").strip()}
        deleted = [row for row in rows if not (row.get("week_summary") or "").strip()
                   or row["id"] != latest.get(row["report_date"])]
        self.report["steps"]["weekly-cleanup"] = {"delete_candidates": len(deleted)}
        if self.args.apply and deleted:
            self._backup("weekly_cleanup_backup.json", deleted)

        def apply():
            self._delete("weekly_reports", deleted)

        return apply

    def _source_is_dead(self, channel_id):
        try:
            response = self.youtube.channels().list(part="contentDetails", id=channel_id).execute()
            items = response.get("items") if isinstance(response, dict) else None
            if items == []:
                return True
            if not isinstance(items, list):
                raise ValueError("YouTube channels.list 응답에 items 누락")
            uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            self.youtube.playlistItems().list(part="id", playlistId=uploads_id, maxResults=1).execute()
        except HttpError as exc:
            if _not_found(exc):
                return True
            raise
        return False

    def dead_sources(self):
        rows = self._rows("trend_sources", active=True)
        candidates = [row for row in rows if (row.get("total_collected") or 0) == 0
                      or row["id"] in self.args.source_ids]
        stats = self.report["steps"]["dead-sources"] = {
            "candidates": len(candidates), "candidate_ids": [row["id"] for row in candidates],
            "confirmed_dead": 0, "confirmed_dead_ids": [], "probe_errors": 0, "disabled": 0,
        }
        if self.youtube is None and candidates and config.YOUTUBE_API_KEY:
            self.youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY, cache_discovery=False)
        dead = []
        if self.youtube is not None:
            for row in candidates:
                if row.get("source_type", "youtube_channel") != "youtube_channel" or not row.get("source_ref"):
                    continue
                try:
                    if self._source_is_dead(row["source_ref"]):
                        dead.append(row)
                except Exception as exc:
                    stats["probe_errors"] += 1
                    print(safe_error(f"YouTube 소스 확인 실패: {safe_error(exc)}"), file=sys.stderr)
        stats.update(confirmed_dead=len(dead), confirmed_dead_ids=[row["id"] for row in dead])
        if self.args.apply and dead:
            self._backup("dead_sources_backup.json", dead)

        def apply():
            for row in dead:
                self._update("trend_sources", "id", row["id"], {"active": False})
                stats["disabled"] += 1

        return apply


def _private_file(path, flags):
    return os.open(path, flags, 0o600)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        super().error(safe_error(message))


def _arguments(argv):
    parser = _Parser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", dest="apply", action="store_false")
    parser.set_defaults(apply=False)
    parser.add_argument("--steps", default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=4.0)
    parser.add_argument("--with-comments", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--source-ids")
    parser.add_argument("--delete-orphan-scores", action="store_true")
    parser.add_argument("--allow-without-migration", action="store_true")
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args(argv)
    args.steps = STEPS if args.steps == "all" else tuple(step.strip() for step in args.steps.split(","))
    if set(args.steps) - set(STEPS):
        parser.error(f"--steps 허용값: all, {', '.join(STEPS)}")
    if args.limit is not None and args.limit < 0:
        parser.error("--limit는 0 이상이어야 합니다")
    if not math.isfinite(args.delay):
        parser.error("--delay는 유한한 숫자여야 합니다")
    try:
        args.source_ids = {int(source_id) for source_id in args.source_ids.split(",")} if args.source_ids else set()
    except ValueError:
        parser.error("--source-ids는 쉼표로 구분된 정수 목록이어야 합니다")
    return args


def _validate_json_report_path(path: Path):
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"JSON 리포트 경로가 이미 존재합니다: {path}")
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise FileNotFoundError(f"JSON 리포트 상위 디렉토리가 없습니다: {parent}")
    if not os.access(parent, os.W_OK):
        raise PermissionError(f"JSON 리포트 상위 디렉토리에 쓰기 권한이 없습니다: {parent}")


def main(argv=None) -> int:
    args = _arguments(argv)
    report = {"apply": args.apply, "steps": {}}
    report_path_valid = True
    try:
        if args.json_report:
            _validate_json_report_path(args.json_report)
    except Exception as exc:
        report_path_valid = False
        report["error"] = safe_error(exc)
        ok = False
    else:
        try:
            client = db.get_client()
            if client is None:
                raise RuntimeError("Supabase 연결 설정 누락")
            repair = Repair(client, args)
            report = repair.report
            ok = repair.run()
        except Exception as exc:
            report["error"] = safe_error(exc)
            ok = False

    if args.json_report and report_path_valid:
        try:
            # Exclusive creation also protects backup files from report path aliases.
            with args.json_report.open("x", encoding="utf-8") as output:
                json.dump(report, output, indent=2, ensure_ascii=False)
        except Exception as exc:
            print(safe_error(f"JSON 리포트 저장 실패: {safe_error(exc)}"), file=sys.stderr)
            ok = False
    for line in json.dumps(report, indent=2, ensure_ascii=False).splitlines():
        print(safe_error(line))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
