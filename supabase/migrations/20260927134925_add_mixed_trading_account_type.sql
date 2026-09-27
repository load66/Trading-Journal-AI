alter table journal.accounts
    drop constraint if exists accounts_type_check;

alter table journal.accounts
    add constraint accounts_type_check
    check (type in ('day_trading','swing_trading','mixed_trading','investment'));

insert into journal.schema_migrations (migration_id)
values ('20260927_006_mixed_trading_account_type')
on conflict (migration_id) do nothing;
