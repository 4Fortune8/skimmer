import json


def normalize_channel_profile(
    *,
    source,
    channel_id,
    channel_name=None,
    subscribers_total=None,
    subscribers_change=None,
    subscribers_change_percentage=None,
    views_total=None,
    views_change=None,
    views_change_percentage=None,
    earnings_low=None,
    earnings_high=None,
    engagement=None,
    upload_frequency=None,
    average_length=None,
    source_url=None,
    raw_rendered_text=None,
):
    return {
        "source": source,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "subscribers_total": subscribers_total,
        "subscribers_change": subscribers_change,
        "subscribers_change_percentage": subscribers_change_percentage,
        "views_total": views_total,
        "views_change": views_change,
        "views_change_percentage": views_change_percentage,
        "earnings_low": earnings_low,
        "earnings_high": earnings_high,
        "engagement": engagement,
        "upload_frequency": upload_frequency,
        "average_length": average_length,
        "source_url": source_url,
        "raw_rendered_text": raw_rendered_text or [],
    }


def print_normalized_profile(profile):
    print(
        "Normalized profile payload:",
        json.dumps(profile, ensure_ascii=False, sort_keys=True),
    )
