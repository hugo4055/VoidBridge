import re


def room_handle_for_discord_channel(channel_id):
    """Build a stable, unlisted GoLive room handle for one Discord voice channel."""
    value = str(channel_id).strip()
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Discord channel ID must contain only ASCII digits.")

    handle = f"priv-vb-{value}"
    if len(handle) > 32:
        raise ValueError("Discord channel ID is too long for a GoLive room handle.")
    return handle


def room_url(handle):
    value = str(handle).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
        raise ValueError("Invalid GoLive room handle.")
    return f"https://golive.nemtudo.me/watch/{value}"


def has_human_members(members):
    """Return True when at least one non-bot member is present."""
    return any(not getattr(member, "bot", False) for member in members)
