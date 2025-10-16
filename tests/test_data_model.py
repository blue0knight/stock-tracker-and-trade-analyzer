"""Unit tests for market-aware timestamp and simulation state logic.

Tests validate:
1. Market hour boundary conditions
2. Weekend rollovers
3. Simulation state transitions
4. Pause detection thresholds
"""
import pytest
from datetime import datetime, timedelta
import pytz
from src.core.data_model import MarketTimestamp, SimulationState

@pytest.fixture
def ny_tz():
    """Fixture for NY timezone."""
    return pytz.timezone('America/New_York')

def test_market_timestamp_validation(ny_tz):
    """Test market hours validation with edge cases."""
    # Valid market hours (should be True)
    cases = [
        (datetime(2025, 10, 15, 9, 30), True),   # Wed 9:30 AM - Market open
        (datetime(2025, 10, 15, 16, 0), True),   # Wed 4:00 PM - Market close
        (datetime(2025, 10, 15, 12, 0), True),   # Wed noon - Mid-market
    ]
    
    for dt, expected in cases:
        dt = ny_tz.localize(dt)
        ts = MarketTimestamp(dt)
        assert ts.is_market_hours() == expected, f"Failed for {dt}"
    
    # Invalid times (should be False)
    invalid_cases = [
        (datetime(2025, 10, 15, 9, 29), False),   # Wed 9:29 AM - Just before open
        (datetime(2025, 10, 15, 16, 1), False),   # Wed 4:01 PM - Just after close
        (datetime(2025, 10, 19, 12, 0), False),   # Sunday - Weekend
        (datetime(2025, 10, 20, 7, 0), False),    # Monday pre-market
    ]
    
    for dt, expected in invalid_cases:
        dt = ny_tz.localize(dt)
        ts = MarketTimestamp(dt)
        assert ts.is_market_hours() == expected, f"Failed for {dt}"

def test_simulation_state():
    """Test simulation state transitions and pause detection."""
    # Initialize with Wed-Thu window
    sim = SimulationState('2025-10-15', '2025-10-16')
    
    # Should start at market open
    assert sim.current.datetime.hour == 9
    assert sim.current.datetime.minute == 30
    assert not sim.paused
    
    # Advance normally (no pause)
    now = datetime.now(pytz.UTC)
    sim.detect_pause(now)
    assert sim.advance(interval_mins=5)  # Still within range
    
    # Detect pause with 20min gap (>15min threshold)
    sim.detect_pause(now)  # First check
    sim.detect_pause(now + timedelta(minutes=20))  # Gap check
    assert sim.paused, "Should detect pause after 20min gap"
    
    # Verify simulation end detection
    far_sim = SimulationState('2025-10-15', '2025-10-15')  # Same day
    while far_sim.advance(5):  # Run until market close
        continue
    assert not far_sim.advance(5), "Should end at market close"

def test_next_market_timestamp(ny_tz):
    """Test market timestamp advancement with weekend handling."""
    # Friday market close
    friday_close = ny_tz.localize(
        datetime(2025, 10, 18, 16, 0)  # Fri 4:00 PM
    )
    ts = MarketTimestamp(friday_close)
    
    # Should skip weekend and open Monday (weekend-only logic)
    next_ts = ts.next_market_timestamp(5)
    assert next_ts.datetime.strftime('%Y-%m-%d %H:%M') == '2025-10-20 09:30'
    
    # Mid-day should advance normally
    midday = ny_tz.localize(
        datetime(2025, 10, 15, 12, 0)  # Wed noon
    )
    ts = MarketTimestamp(midday)
    next_ts = ts.next_market_timestamp(5)
    assert next_ts.datetime.hour == 12
    assert next_ts.datetime.minute == 5
    
    # End of day should jump to next morning (next weekday)
    eod = ny_tz.localize(
        datetime(2025, 10, 15, 15, 59)  # Wed 3:59 PM
    )
    ts = MarketTimestamp(eod)
    next_ts = ts.next_market_timestamp(5)
    # Next open is Thursday 2025-10-16 09:30 (next weekday)
    assert next_ts._dt.strftime('%Y-%m-%d %H:%M') == '2025-10-16 09:30'