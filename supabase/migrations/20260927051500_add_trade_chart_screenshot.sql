alter table journal.trade_analysis
  add column if not exists chart_screenshot_path text;

insert into journal.schema_migrations (migration_id)
values ('20260927_004_trade_chart_screenshot')
on conflict (migration_id) do nothing;
