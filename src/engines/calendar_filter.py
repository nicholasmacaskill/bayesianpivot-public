"""
Economic Calendar Filter
========================
Hard-gates any signal that would fire within the blackout window of a
high-impact economic event (FOMC, NFP, CPI, ETF rebalance dates).

Sources (in priority order):
  1. ForexFactory JSON feed (weekly high-impact events, free)
  2. Static crypto event overrides (manually maintained)

Window: 30 minutes before → 30 minutes after each event.
"""

import logging
import requests
import pytz
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# Static list of known crypto-specific high-impact dates (format: MM-DD)
# Add ETF rebalancing, BTC halving weeks, major unlock events here.
CRYPTO_BLACKOUT_DATES = {
    # "03-08": "Bitcoin ETF Rebalancing Window",   # example
}

# High-impact event title keywords that trigger a blackout even if impact != 'High'
KEYWORD_OVERRIDES = [
    "fomc", "fed ", "interest rate", "nonfarm", "nfp", "cpi", "inflation",
    "gdp", "unemployment", "payroll", "jerome powell", "yellen", "ecb",
    "boe", "rba", "boj", "snb rate"
]

# Currencies to monitor (crypto trades inverse to USD, so USD is primary)
MONITORED_CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD']

BLACKOUT_MINUTES = 30  # Minutes before AND after an event to block trading


class CalendarFilter:
    """
    Checks if current time is safe to trade relative to macro events.

    Usage:
        cal = CalendarFilter()
        is_safe, reason = cal.is_safe_to_trade()
        if not is_safe:
            logger.warning(f"CALENDAR BLOCKED: {reason}")
    """

    CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    def __init__(self, blackout_minutes: int = BLACKOUT_MINUTES):
        self.blackout_minutes = blackout_minutes
        self._events: list[dict] = []
        self._last_fetch: Optional[datetime] = None
        self._fetch_interval_hours = 6  # Refresh every 6 hours

    def _should_refresh(self) -> bool:
        if not self._last_fetch:
            return True
        return (datetime.utcnow() - self._last_fetch).total_seconds() > self._fetch_interval_hours * 3600

    def _fetch_events(self):
        """Fetch and cache high-impact events from ForexFactory."""
        try:
            resp = requests.get(self.CALENDAR_URL, timeout=10)
            if resp.status_code == 200:
                raw = resp.json()
                events = []
                for e in raw:
                    currency = e.get('country', '')
                    impact = e.get('impact', '')
                    title = e.get('title', '').lower()
                    date_str = e.get('date', '')

                    is_high_impact = (impact == 'High' and currency in MONITORED_CURRENCIES)
                    is_keyword_match = any(kw in title for kw in KEYWORD_OVERRIDES)

                    if is_high_impact or is_keyword_match:
                        try:
                            # FF dates come as ISO with or without timezone
                            event_time = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                            events.append({
                                'title': e.get('title', 'Unknown'),
                                'currency': currency,
                                'impact': impact,
                                'time_utc': event_time.astimezone(pytz.utc).replace(tzinfo=None),
                            })
                        except Exception as parse_err:
                            logger.debug(f"CalendarFilter: Could not parse date '{date_str}': {parse_err}")

                self._events = events
                self._last_fetch = datetime.utcnow()
                logger.info(f"[CalendarFilter] Loaded {len(events)} high-impact events for the week.")
        except Exception as e:
            logger.warning(f"[CalendarFilter] Failed to fetch calendar: {e}. Using cached/empty list.")

    def _check_crypto_blackout(self) -> tuple[bool, str]:
        """Check static crypto-specific blackout dates."""
        today_key = datetime.utcnow().strftime('%m-%d')
        if today_key in CRYPTO_BLACKOUT_DATES:
            reason = f"Crypto blackout date: {CRYPTO_BLACKOUT_DATES[today_key]}"
            return False, reason
        return True, "OK"

    def check(self, symbol: str = None) -> tuple[bool, str]:
        """Alias for is_safe_to_trade() for compatibility with ExecutionFirewall."""
        return self.is_safe_to_trade(symbol)

    def is_safe_to_trade(self, symbol: str = None) -> tuple[bool, str]:
        """
        Returns (is_safe: bool, reason: str).
        is_safe=False means BLOCK the signal.
        """
        # Refresh if stale
        if self._should_refresh():
            self._fetch_events()

        # 1. Check crypto-specific blackout dates
        safe, reason = self._check_crypto_blackout()
        if not safe:
            logger.warning(f"⛔ [CalendarFilter] {reason}")
            return False, reason

        # 2. Check ForexFactory events
        now_utc = datetime.utcnow()
        window = timedelta(minutes=self.blackout_minutes)

        # Determine relevant currencies for the target asset (default USD)
        relevant_currencies = {'USD'}
        if symbol:
            sym_clean = symbol.upper()
            for curr in MONITORED_CURRENCIES:
                if curr in sym_clean:
                    relevant_currencies.add(curr)

        for event in self._events:
            event_curr = str(event.get('currency', '')).upper()
            event_title = str(event.get('title', '')).lower()
            is_global_event = any(kw in event_title for kw in ['fomc', 'fed ', 'jerome powell', 'interest rate decision'])
            if symbol and event_curr not in relevant_currencies and not is_global_event:
                continue

            event_time = event['time_utc']
            diff_minutes = (event_time - now_utc).total_seconds() / 60

            # Within blackout window (before OR after)
            if -self.blackout_minutes <= diff_minutes <= self.blackout_minutes:
                if diff_minutes >= 0:
                    direction = f"in {int(diff_minutes)}m"
                else:
                    direction = f"{int(abs(diff_minutes))}m ago"

                reason = (
                    f"⛔ MACRO BLACKOUT: '{event['title']}' ({event['currency']}) "
                    f"— {direction}. Blocking trade to avoid binary event risk."
                )
                logger.warning(f"[CalendarFilter] {reason}")
                return False, reason

        return True, f"Clear — next event check at {(self._last_fetch + timedelta(hours=self._fetch_interval_hours)).strftime('%H:%M UTC') if self._last_fetch else 'N/A'}"

    def next_event_info(self) -> Optional[dict]:
        """Returns the next upcoming high-impact event within 24h, for dashboard display."""
        if self._should_refresh():
            self._fetch_events()

        now_utc = datetime.utcnow()
        upcoming = [
            e for e in self._events
            if 0 < (e['time_utc'] - now_utc).total_seconds() / 3600 < 24
        ]
        if not upcoming:
            return None

        next_e = min(upcoming, key=lambda e: e['time_utc'])
        mins_until = int((next_e['time_utc'] - now_utc).total_seconds() / 60)
        return {
            'title': next_e['title'],
            'currency': next_e['currency'],
            'impact': next_e['impact'],
            'minutes_until': mins_until,
            'time_utc': next_e['time_utc'].isoformat(),
        }
