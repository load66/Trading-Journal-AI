alter table journal.trades
    add column if not exists excursion_version text;

insert into journal.schema_migrations (migration_id)
values ('20260927_005_excursion_engine_version')
on conflict (migration_id) do nothing;
