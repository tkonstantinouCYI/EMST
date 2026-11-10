"""UTC identities, local market dates and explicit synthetic DST policy."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

CYPRUS = ZoneInfo("Europe/Nicosia")
UTC = timezone.utc


@dataclass(frozen=True)
class Interval:
    id: str
    start: datetime
    minutes: int
    period: int

    @property
    def hours(self):
        return self.minutes / 60

    @property
    def end(self):
        return self.start + timedelta(minutes=self.minutes)

    @property
    def local(self):
        return self.start.astimezone(CYPRUS)


def day_intervals(day: date, minutes: int, mode="qualified_synthetic"):
    if minutes not in (5, 30):
        raise ValueError("Only 5-minute RTUs and 30-minute periods are supported")
    start = datetime.combine(day, time(), CYPRUS).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(), CYPRUS).astimezone(UTC)
    count = int((end-start).total_seconds() / (60*minutes))
    if count*minutes != 1440 and mode != "qualified_synthetic":
        raise ValueError("Verified official DST period table is required")
    return [Interval(f"{day}:{minutes}:{i:03}", start+timedelta(minutes=i*minutes),
                     minutes, i if minutes == 30 else i//6) for i in range(count)]


def next_business_day(day, holidays=frozenset()):
    day += timedelta(days=1)
    while day.weekday() >= 5 or day in holidays:
        day += timedelta(days=1)
    return day


def historical_days(day, business_days):
    """Explicit classification table; absent classifications must not become weekends."""
    wanted = business_days[day]
    result = []
    cursor = day
    while len(result) < 3:
        cursor -= timedelta(days=1)
        if cursor not in business_days:
            raise ValueError(f"Missing business-day classification: {cursor}")
        if business_days[cursor] == wanted:
            result.append(cursor)
    return result


def supplied_period_table(day,rows):
    """Replay verified official IDs/instants, including transition-day numbering."""
    periods=[]
    for i,row in enumerate(rows):
        start=datetime.fromisoformat(row["start"]).astimezone(UTC)
        end=datetime.fromisoformat(row["end"]).astimezone(UTC)
        minutes=int((end-start).total_seconds()/60)
        if minutes!=30 or not row["official_id"] or not row["verification_source"]:
            raise ValueError("Verified half-hour official table required")
        periods.append(Interval(row["official_id"],start,minutes,i))
    if not periods or len({p.id for p in periods})!=len(periods):raise ValueError("Duplicate/empty calendar")
    if any(a.end!=b.start for a,b in zip(periods,periods[1:])):raise ValueError("Calendar gap/overlap")
    begin=datetime.combine(day,time(),CYPRUS).astimezone(UTC)
    finish=datetime.combine(day+timedelta(days=1),time(),CYPRUS).astimezone(UTC)
    if periods[0].start!=begin or periods[-1].end!=finish:raise ValueError("Incomplete official day")
    rtus=[Interval(f"{p.id}:RTU:{j}",p.start+timedelta(minutes=5*j),5,p.period) for p in periods for j in range(6)]
    return periods,rtus
