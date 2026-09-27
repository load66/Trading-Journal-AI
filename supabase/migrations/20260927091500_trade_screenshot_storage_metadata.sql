alter table journal.trade_analysis
  add column if not exists chart_screenshot_provider text,
  add column if not exists chart_screenshot_bytes bigint,
  add column if not exists chart_screenshot_width integer,
  add column if not exists chart_screenshot_height integer,
  add column if not exists chart_screenshot_content_type text,
  add column if not exists chart_screenshot_sha256 text,
  add column if not exists chart_screenshot_uploaded_at timestamptz;

update journal.trade_analysis
set chart_screenshot_provider = 'supabase'
where chart_screenshot_path is not null
  and chart_screenshot_provider is null;

insert into journal.schema_migrations (migration_id)
values ('20260927_005_trade_screenshot_storage_metadata')
on conflict (migration_id) do nothing;
