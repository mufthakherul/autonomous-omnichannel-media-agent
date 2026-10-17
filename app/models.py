from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class VideoInspectionAndSEO(BaseModel):
    """Structured output returned by Gemini 3.8 Flash after inspecting a video."""

    can_upload_as_is: bool = Field(
        description="True ONLY if the video can be uploaded directly to YouTube without any visual or audio modifications."
    )
    readiness_score: int = Field(
        description="Overall upload readiness and quality score from 0 to 100."
    )
    watermark_or_logo_detected: bool = Field(
        description="True if third-party watermarks, burned-in social handles (TikTok/Reels), or unwanted logos appear."
    )
    audio_copyright_risk: Literal["low", "medium", "high"] = Field(
        description="Estimated risk of audio triggering YouTube Content ID (low for royalty-free/ambient/natural/silent, high for commercial songs)."
    )
    policy_safe: bool = Field(
        description="True if the video complies with YouTube Community Guidelines and advertiser-friendly guidelines."
    )
    issues_detected: List[str] = Field(
        description="List of specific issues found that prevent uploading as-is (empty if can_upload_as_is is True)."
    )
    recommended_fixes: List[
        Literal[
            "none",
            "copyright_safe_remix",
            "replace_or_mix_sfx_audio",
            "mute_audio",
            "trim_silence",
            "crop_watermark",
            "normalize_aspect_ratio",
            "transcode_h264",
        ]
    ] = Field(
        description="FFmpeg remediation and copyright-safe transformation actions that make the video 100% unique and upload-ready."
    )
    content_summary: str = Field(
        description="Concise 1-2 sentence description of what visually and audibly happens in the video."
    )
    seo_title: str = Field(
        description="High-CTR, accurate, SEO-optimized YouTube title (max 95 characters; include #Shorts if vertical short clip)."
    )
    seo_description: str = Field(
        description="Engaging YouTube description with hook, detailed overview, timestamps/chapters, and 3-5 relevant hashtags."
    )
    seo_tags: List[str] = Field(
        description="10 to 15 high-relevance YouTube search tags without the # symbol."
    )
    category_id: str = Field(
        description="Best matching YouTube numeric Category ID (e.g. '22' People & Blogs, '28' Science & Tech, '15' Pets & Animals, '19' Travel & Events, '27' Education, '24' Entertainment)."
    )
    suggested_playlist: str = Field(
        description="Clean playlist title where this video belongs (e.g., 'Nature & Landscapes 4K', 'Tech Shorts', 'Relaxing Ambient')."
    )
    best_thumbnail_timestamp_sec: float = Field(
        description="Timestamp in seconds of the single most visually striking, clear frame to extract as the video thumbnail."
    )


class CommentTriageDecision(BaseModel):
    """Structured output for a single YouTube comment evaluated by Gemini."""

    comment_id: str = Field(description="The ID of the comment being evaluated.")
    needs_reply: bool = Field(
        description="True ONLY if this comment genuinely warrants a creator reply (e.g., a question, thoughtful feedback, timestamp inquiry, or meaningful praise). False for spam, self-promo, trolls, bots, or generic single-emoji reactions."
    )
    category: Literal[
        "question",
        "thoughtful_feedback",
        "meaningful_praise",
        "troubleshooting",
        "simple_reaction",
        "spam_or_promo",
        "troll_or_hostile",
    ] = Field(description="Classification category of the comment.")
    priority: int = Field(
        description="Reply priority from 1 (lowest) to 5 (highest, e.g., urgent viewer question)."
    )
    sentiment: Literal["positive", "neutral", "negative"] = Field(
        description="Overall sentiment of the viewer comment."
    )
    reason: str = Field(
        description="Brief explanation of why this comment does or does not need a reply."
    )
    draft_reply: str = Field(
        description="If needs_reply is True, a natural, helpful, human-sounding reply matching the channel persona. Empty string if needs_reply is False."
    )


class CommentTriageBatch(BaseModel):
    """Batch response of evaluated YouTube comments."""

    decisions: List[CommentTriageDecision]


class CreateSourceRequest(BaseModel):
    name: str
    source_type: Literal["local", "gdrive", "s3", "pexels", "pixabay", "telegram", "rss", "url"]
    config_value: str = Field(
        description="Path, Folder ID, Search Query, Bucket/Prefix, RSS URL, or Direct Video URL"
    )
    max_items_per_sync: int = 5
    active: bool = True


class UpdateVideoMetadataRequest(BaseModel):
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    seo_tags: Optional[List[str]] = None
    category_id: Optional[str] = None
    playlist_name: Optional[str] = None
    can_upload_as_is: Optional[bool] = None


class SimulateCommentsRequest(BaseModel):
    video_title: str = "4K Drone Footage: Misty Alpine Mountains at Sunrise"
    comments: List[dict]


class BulkSourcesRequest(BaseModel):
    raw_links: str = Field(
        description="Space-, comma-, or newline-separated list of Google Drive folder URLs, Telegram links, or web URLs"
    )
    max_items_per_sync: int = 4


class OmnichannelPublishRequest(BaseModel):
    platforms: List[str] = Field(
        default_factory=lambda: ["youtube", "facebook", "instagram", "tiktok"]
    )
    schedule: bool = False


class AddApiKeysRequest(BaseModel):
    raw_keys: str = Field(
        description="One or more API keys (one per line, or 'Label | AIza...' format)"
    )
    provider: Literal["gemini", "pexels", "pixabay", "other"] = "gemini"


class TrendBrief(BaseModel):
    """Structured output for a trending news story or viral event brief."""

    headline: str = Field(description="Punchy, accurate headline of the news story or event.")
    niche: str = Field(description="Category or niche (e.g. 'Tech & AI', 'World News', 'Science', 'Pop Culture').")
    summary: str = Field(description="Concise 2-sentence factual summary of the event.")
    why_trending: str = Field(description="Why audiences care about this right now.")
    caption: str = Field(
        description="Engaging social media post caption with hook, key takeaways, and engagement question."
    )
    hashtags: List[str] = Field(description="6 to 10 high-traffic hashtags without the # symbol.")
    image_prompt: str = Field(
        description="Detailed visual prompt for Gemini Nano Banana 2.1 to generate a striking editorial graphic or photo."
    )
    video_prompt: str = Field(
        description="Cinematic motion prompt for Gemini Omni 1.1 Flash to generate a dynamic video scene."
    )
    voiceover_script: str = Field(
        description="Natural, high-retention 15-25 second spoken narration script for Gemini 3.8 Flash TTS."
    )


class TrendBatch(BaseModel):
    trends: List[TrendBrief]


class GenerateStudioContentRequest(BaseModel):
    topic: str = Field(description="News headline, event, or custom topic to generate content about.")
    niche: str = "Trending News & Events"
    creation_type: Literal["ai_image_post", "ai_video", "both"] = "ai_image_post"
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    use_search_grounding: bool = True
    auto_publish: bool = False
    platforms: List[str] = Field(
        default_factory=lambda: ["youtube", "facebook", "instagram", "tiktok"]
    )


