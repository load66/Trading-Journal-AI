create index idx_daily_summaries_account_id
  on journal.daily_summaries(account_id);

create index idx_diary_entries_account_id
  on journal.diary_entries(account_id);

create index idx_trade_analysis_diary_entry_id
  on journal.trade_analysis(diary_entry_id);
