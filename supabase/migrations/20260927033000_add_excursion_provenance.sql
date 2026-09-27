alter table journal.trades
    add column if not exists excursion_basis text,
    add column if not exists excursion_calculated_at timestamptz;

insert into journal.schema_migrations (migration_id)
values ('20260927_003_excursion_provenance')
on conflict (migration_id) do nothing;
