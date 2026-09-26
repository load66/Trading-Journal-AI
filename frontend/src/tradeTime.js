export const BROKER_EXECUTION_TIME_ZONE = 'America/Chicago';
export const DISPLAY_TIME_ZONE = 'America/New_York';

export function zonedWallTimeToDate(dateStr, timeStr, timeZone = BROKER_EXECUTION_TIME_ZONE) {
  if (!dateStr || !timeStr) return null;
  const [year, month, day] = dateStr.split('-').map(Number);
  const [hour, minute, second = 0] = timeStr.split(':').map(Number);
  if (![year, month, day, hour, minute, second].every(Number.isFinite)) return null;

  const wallUtcMs = Date.UTC(year, month - 1, day, hour, minute, second);
  let utcMs = wallUtcMs;
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hourCycle: 'h23',
  });

  // Two passes resolve the zone offset without hard-coding CST/CDT.
  for (let i = 0; i < 2; i += 1) {
    const parts = Object.fromEntries(
      formatter.formatToParts(new Date(utcMs))
        .filter((p) => p.type !== 'literal')
        .map((p) => [p.type, p.value])
    );
    const renderedAsUtc = Date.UTC(
      Number(parts.year), Number(parts.month) - 1, Number(parts.day),
      Number(parts.hour), Number(parts.minute), Number(parts.second)
    );
    utcMs += wallUtcMs - renderedAsUtc;
  }
  return new Date(utcMs);
}

export function executionETParts(dateStr, timeStr) {
  const instant = zonedWallTimeToDate(dateStr, timeStr);
  if (!instant) return null;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: DISPLAY_TIME_ZONE,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(instant)
      .filter((p) => p.type !== 'literal')
      .map((p) => [p.type, p.value])
  );
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    second: Number(parts.second),
    hhmm: `${parts.hour}:${parts.minute}`,
  };
}

export function formatExecutionTimeET(dateStr, timeStr) {
  const parts = executionETParts(dateStr, timeStr);
  return parts ? `${parts.hhmm} ET` : '—';
}

export function executionTimeETMinutes(dateStr, timeStr) {
  const parts = executionETParts(dateStr, timeStr);
  return parts ? parts.hour * 60 + parts.minute : null;
}

// lightweight-charts reads its time axis as UTC. The intraday market bars are
// deliberately shifted to an ET wall-clock timeline, so broker fills must be
// normalized from Schwab Central time to ET first, then bucketed on that same
// wall-clock timeline.
export function executionToChartTs(dateStr, timeStr, bucketMin = 5) {
  const parts = executionETParts(dateStr, timeStr);
  if (!parts) return null;
  const totalMin = Math.floor((parts.hour * 60 + parts.minute) / bucketMin) * bucketMin;
  const hour = Math.floor(totalMin / 60);
  const minute = totalMin % 60;
  const [year, month, day] = parts.date.split('-').map(Number);
  return Math.floor(Date.UTC(year, month - 1, day, hour, minute, 0) / 1000);
}
