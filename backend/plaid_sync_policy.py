"""Pure, fail-closed Plaid existing-Item call-budget policy.

No network or storage access. Storage/lease reservations must be persisted
atomically by the caller *before* every /transactions/sync page request.
"""
ITEM_POLL_SECONDS = 3 * 3600
MIN_REFRESH_GAP_SECONDS = 5 * 60
ROLLING_WINDOW_SECONDS = 24 * 3600
MAX_DAILY_CALLS = 24
RETRY_HOURS = (1, 3, 6, 24)


class DailyCallCapReached(Exception):
    """A safe user-facing status, with no Item identifier or provider details."""
    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__("Daily transaction sync call cap reached")


def rolling_calls(records, now):
    """Retain only the last rolling day; corrupt timestamps fail closed."""
    if records is None:
        records = []
    if not isinstance(records, list) or type(now) not in (int, float):
        raise ValueError("Invalid rolling call budget")
    valid = []
    for stamp in records:
        if type(stamp) not in (int, float) or not 0 <= stamp <= now:
            raise ValueError("Invalid rolling call budget")
        if stamp > now - ROLLING_WINDOW_SECONDS:
            valid.append(stamp)
    if len(valid) > MAX_DAILY_CALLS:
        raise ValueError("Daily sync budget invariant violated")
    return valid


def reserve_call(records, now):
    """Return the amended ledger to persist before making a provider call."""
    recent = rolling_calls(records, now)
    if len(recent) >= MAX_DAILY_CALLS:
        raise DailyCallCapReached(min(recent) + ROLLING_WINDOW_SECONDS)
    return [*recent, now]


def retry_deadline(now, attempts):
    if type(attempts) is not int or attempts < 0:
        raise ValueError("Invalid retry attempts")
    if not attempts:
        return now + ITEM_POLL_SECONDS
    return now + RETRY_HOURS[min(attempts, len(RETRY_HOURS)) - 1] * 3600


def due_for_refresh(last_synced_at, now):
    if last_synced_at is None:
        return True
    if type(last_synced_at) not in (int, float) or type(now) not in (int, float):
        return False
    return now - last_synced_at >= MIN_REFRESH_GAP_SECONDS
