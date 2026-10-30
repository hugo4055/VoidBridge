-- Additive, non-destructive settings for VoidBridge voice-room sharing.
-- Existing message maps, channel maps, roles, and account links are untouched.

create table if not exists public.bridge_settings (
    guild_id text primary key,
    voice_rooms_enabled boolean not null default true,
    voice_announce_channel_id text,
    updated_by text,
    updated_at timestamptz not null default now()
);

alter table public.bridge_settings enable row level security;
