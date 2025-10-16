"""Core data models for market-aware timestamp handling and simulation state.

Note on timezone handling:
Currently using pytz for timezone management to maintain compatibility with
existing codebase and Polygon API responses. Future migration path:
1. Python 3.9+: Replace pytz with zoneinfo (PEP 615)
2. Update polygon_adapter.py to convert API responses
3. Update scheduler.py market hour checks
4. Remove pytz dependency

Migration blocked on: Polygon API response format validation
"""
from datetime import datetime, time, timedelta
from typing import Optional
import pytz


class MarketTimestamp:
    """Market-aware timestamp handling with timezone validation.
    
    Enforces US market hour constraints and provides safe timestamp
    arithmetic that respects market open/close boundaries.
    """
    
    NY_TZ = pytz.timezone('America/New_York')
    MARKET_OPEN = time(9, 30)    # 9:30 AM ET
    MARKET_CLOSE = time(16, 0)   # 4:00 PM ET
    
    def __init__(self, dt: datetime):
        """Initialize with timezone-aware datetime.
        
        Args:
            dt: Datetime object (will be converted to ET if naive)
        """
        if dt.tzinfo is None:
            dt = self.NY_TZ.localize(dt)
        elif dt.tzinfo != self.NY_TZ:
            dt = dt.astimezone(self.NY_TZ)
        self._dt = dt
        
    @classmethod
    def from_str(cls, date_str: str) -> 'MarketTimestamp':
        """Create from YYYY-MM-DD string, normalized to market open.
        
        Args:
            date_str: Date string in YYYY-MM-DD format
            
        Returns:
            MarketTimestamp set to market open (9:30 AM ET)
        """
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        dt = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        return cls(dt)
        
    def is_market_hours(self) -> bool:
        """Check if timestamp falls within market hours.
        
        Returns:
            True if Mon-Fri and between 9:30 AM - 4:00 PM ET
        """
        t = self._dt.time()
        return (
            self.MARKET_OPEN <= t <= self.MARKET_CLOSE and
            self._dt.weekday() < 5  # Mon-Fri
        )
        
    def next_market_timestamp(self, interval_mins: int = 5) -> 'MarketTimestamp':
        """Get next valid market timestamp.
        
        Handles:
        - Intraday intervals
        - End-of-day rollovers
        - Weekend/holiday skips
        
        Args:
            interval_mins: Minutes to advance (default: 5)
            
        Returns:
            Next valid MarketTimestamp
        """
        next_dt = self._dt + timedelta(minutes=interval_mins)
        result = MarketTimestamp(next_dt)
        
        # Still in market hours
        if result.is_market_hours():
            return result
            
        # After market close - get next market open
        return self._get_next_market_open()
        
    def _get_next_market_open(self) -> 'MarketTimestamp':
        """Get next market open timestamp.
        
        Handles weekend rollovers by advancing day-by-day until
        reaching next weekday.
        
        Returns:
            MarketTimestamp for next market open
        """
        dt = self._dt
        while True:
            dt += timedelta(days=1)
            if dt.weekday() >= 5:  # Skip weekends
                continue
                
            dt = dt.replace(
                hour=self.MARKET_OPEN.hour,
                minute=self.MARKET_OPEN.minute,
                second=0,
                microsecond=0
            )
            return MarketTimestamp(dt)
            
    @property
    def datetime(self) -> datetime:
        """Get underlying datetime object."""
        return self._dt
        
    def __str__(self) -> str:
        """Format as YYYY-MM-DD HH:MM:SS TZ."""
        return self._dt.strftime('%Y-%m-%d %H:%M:%S %Z')


class SimulationState:
    """Maintains simulation state with pause/gap detection.
    
    Tracks:
    - Current simulation time
    - End boundary
    - Data feed health
    - Pause detection
    """
    
    def __init__(self, start_date: str, end_date: str):
        """Initialize simulation period.
        
        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
        """
        self.current = MarketTimestamp.from_str(start_date)
        self.end = MarketTimestamp.from_str(end_date)
        self.paused = False
        self._last_check: Optional[datetime] = None
        
    def advance(self, interval_mins: int = 5) -> bool:
        """Advance simulation time if not paused.
        
        Args:
            interval_mins: Minutes to advance (default: 5)
            
        Returns:
            False if simulation complete, True otherwise
        """
        if self.paused:
            return True
            
        self.current = self.current.next_market_timestamp(interval_mins)
        return self.current.datetime <= self.end.datetime
        
    def detect_pause(self, last_data_time: datetime) -> bool:
        """Detect data feed pauses/gaps.
        
        Args:
            last_data_time: Timestamp of most recent data point
            
        Returns:
            True if pause detected (gap > 15min)
        """
        if self._last_check is None:
            self._last_check = last_data_time
            return False
            
        # Pause if no data for >15 min
        gap = (last_data_time - self._last_check).total_seconds() / 60
        self.paused = gap > 15
        
        self._last_check = last_data_time
        return self.paused