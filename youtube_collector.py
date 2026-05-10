"""
YouTube Data API를 이용해 영상 정보를 수집하는 모듈
"""
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import re
import config


# ===== 영상 길이 필터 =====
MIN_VIDEO_DURATION_SECONDS = 300  # 5분 (300초) 이상만 분석


def parse_duration(duration_str):
    """ISO 8601 duration 문자열을 초 단위로 변환
    예: PT1H2M30S → 3750초, PT45S → 45초
    """
    if not duration_str:
        return 0
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def get_youtube_client():
    """YouTube API 클라이언트 생성"""
    return build('youtube', 'v3', developerKey=config.YOUTUBE_API_KEY)


def search_channel_id(channel_name):
    """채널명으로 채널 ID 검색"""
    youtube = get_youtube_client()
    
    request = youtube.search().list(
        q=channel_name,
        type='channel',
        part='id,snippet',
        maxResults=1
    )
    response = request.execute()
    
    if response.get('items'):
        channel = response['items'][0]
        return {
            'channel_id': channel['id']['channelId'],
            'channel_title': channel['snippet']['title'],
            'description': channel['snippet']['description']
        }
    return None


def get_channel_videos(channel_id, months_back=6):
    """채널의 최근 N개월 영상 목록 가져오기"""
    youtube = get_youtube_client()
    
    # 채널 정보로 uploads 플레이리스트 ID 가져오기
    channel_response = youtube.channels().list(
        id=channel_id,
        part='contentDetails,statistics,snippet'
    ).execute()
    
    if not channel_response.get('items'):
        return [], {}
    
    channel_info = channel_response['items'][0]
    uploads_playlist = channel_info['contentDetails']['relatedPlaylists']['uploads']
    subscriber_count = int(channel_info['statistics'].get('subscriberCount', 0))
    
    # 기간 설정
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=months_back * 30)
    
    videos = []
    next_page_token = None
    
    while True:
        playlist_response = youtube.playlistItems().list(
            playlistId=uploads_playlist,
            part='snippet,contentDetails',
            maxResults=50,
            pageToken=next_page_token
        ).execute()
        
        for item in playlist_response.get('items', []):
            published_at = datetime.fromisoformat(
                item['snippet']['publishedAt'].replace('Z', '+00:00')
            )
            
            if published_at < cutoff_date:
                return videos, {
                    'subscriber_count': subscriber_count,
                    'channel_title': channel_info['snippet']['title']
                }
            
            videos.append({
                'video_id': item['contentDetails']['videoId'],
                'title': item['snippet']['title'],
                'published_at': item['snippet']['publishedAt'],
                'channel_title': item['snippet']['channelTitle']
            })
        
        next_page_token = playlist_response.get('nextPageToken')
        if not next_page_token:
            break
    
    return videos, {
        'subscriber_count': subscriber_count,
        'channel_title': channel_info['snippet']['title']
    }


def get_video_details(video_ids, min_duration=None):
    """영상 상세 정보 일괄 조회 (50개씩).
    min_duration=None이면 기본 5분 필터 적용. 0이면 숏츠 포함 전체.
    """
    if min_duration is None:
        min_duration = MIN_VIDEO_DURATION_SECONDS
    youtube = get_youtube_client()
    all_details = []
    filtered_count = 0

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        response = youtube.videos().list(
            id=','.join(batch),
            part='snippet,statistics,contentDetails'
        ).execute()

        for item in response.get('items', []):
            stats = item.get('statistics', {})
            snippet = item.get('snippet', {})
            content = item.get('contentDetails', {})
            duration_str = content.get('duration', '')
            duration_seconds = parse_duration(duration_str)

            if min_duration > 0 and duration_seconds < min_duration:
                filtered_count += 1
                continue

            all_details.append({
                'video_id': item['id'],
                'title': snippet.get('title', ''),
                'description': snippet.get('description', '')[:500],
                'published_at': snippet.get('publishedAt', ''),
                'channel_title': snippet.get('channelTitle', ''),
                'tags': ','.join(snippet.get('tags', [])[:10]),
                'view_count': int(stats.get('viewCount', 0)),
                'like_count': int(stats.get('likeCount', 0)),
                'comment_count': int(stats.get('commentCount', 0)),
                'duration': duration_str,
                'duration_seconds': duration_seconds,
                'thumbnail_url': snippet.get('thumbnails', {}).get('high', {}).get('url', ''),
                'video_url': f"https://youtube.com/watch?v={item['id']}"
            })

    if filtered_count > 0:
        print(f"  📏 {min_duration//60}분 미만 영상 {filtered_count}개 제외됨")

    return all_details


def is_shorts(video):
    """숏츠 여부 판별: #shorts 태그 또는 60초 이하"""
    title = video.get('title', '').lower()
    tags = video.get('tags', '').lower()
    duration_seconds = video.get('duration_seconds', 0)
    return '#shorts' in title or '#shorts' in tags or duration_seconds <= 60


def get_video_comments(video_id, max_comments=200):
    """영상의 상위 댓글 가져오기"""
    youtube = get_youtube_client()
    comments = []
    next_page_token = None
    
    try:
        while len(comments) < max_comments:
            response = youtube.commentThreads().list(
                videoId=video_id,
                part='snippet',
                order='relevance',
                maxResults=min(100, max_comments - len(comments)),
                pageToken=next_page_token
            ).execute()
            
            for item in response.get('items', []):
                comment = item['snippet']['topLevelComment']['snippet']
                comments.append({
                    'text': comment.get('textDisplay', ''),
                    'like_count': comment.get('likeCount', 0),
                    'published_at': comment.get('publishedAt', '')
                })
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
    except Exception as e:
        # 댓글이 막혀있는 영상도 있음
        print(f"댓글 수집 실패 ({video_id}): {e}")
    
    return comments


def get_trending_videos(category_id="0", region="KR", max_results=50):
    """한국 트렌딩 영상 가져오기"""
    youtube = get_youtube_client()
    
    response = youtube.videos().list(
        chart='mostPopular',
        regionCode=region,
        videoCategoryId=category_id,
        part='snippet,statistics,contentDetails',
        maxResults=max_results
    ).execute()
    
    trending = []
    for item in response.get('items', []):
        snippet = item.get('snippet', {})
        stats = item.get('statistics', {})
        
        trending.append({
            'video_id': item['id'],
            'title': snippet.get('title', ''),
            'channel_title': snippet.get('channelTitle', ''),
            'published_at': snippet.get('publishedAt', ''),
            'view_count': int(stats.get('viewCount', 0)),
            'like_count': int(stats.get('likeCount', 0)),
            'comment_count': int(stats.get('commentCount', 0)),
            'tags': snippet.get('tags', [])[:10],
            'category_id': snippet.get('categoryId', ''),
            'video_url': f"https://youtube.com/watch?v={item['id']}"
        })
    
    return trending
