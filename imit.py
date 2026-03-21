"""
imit.py - Signal simulation generators for IEC 104 driver.

This module provides generators for simulating signal value changes:
- imit_ladder: Linear ramp generator (ladder pattern)
- imit_rand: Random value generator

These generators are used for testing and demonstration purposes.
"""

import random
import time
from typing import Iterator, Tuple, List, Optional
import struct

# Pause (seconds) after every N-th yield — reduces CPU load
SIM_SLEEP = 0
SIM_SLEEP_EVERY_N = 1000  # Sleep only every N-th packet


def imit_ladder(
    cnt_step: int = 1000,
    time_step: float = 1.0,
    val_step: float = 0.1,
    val_min: float = 0.0,
    val_max: float = 5.0,
    list_id: Optional[List[int]] = None
) -> Iterator[Tuple[float, int, float, int]]:
    """Generate ladder (ramp) pattern signal values.

    Creates a linearly increasing value that wraps around when reaching the
    maximum. Useful for testing trend visualization and analog value processing.

    Args:
        cnt_step: Number of steps to generate.
        time_step: Time delay between steps (seconds).
        val_step: Value increment per step.
        val_min: Minimum value (wrap point).
        val_max: Maximum value (wrap point).
        list_id: List of signal IDs to generate values for.

    Yields:
        Tuple of (timestamp, signal_id, value, quality) for each step.

    Example:
        >>> for ts, sid, val, q in imit_ladder(cnt_step=5, time_step=0.1,
        ...                                     val_step=1, val_min=0, val_max=3,
        ...                                     list_id=[1, 2]):
        ...     print(f"ID:{sid} VAL:{val}")
        ID:1 VAL:0.0
        ID:2 VAL:0.0
        ID:1 VAL:1.0
        ID:2 VAL:1.0
        ...
    """
    if list_id is None:
        list_id = []

    val = val_min
    q = 0

    for _ in range(cnt_step):
        t = time.time()
        time.sleep(time_step)

        for sid in list_id:
            yield t, sid, val, q

        # Update value with wrap around
        if val + val_step > val_max:
            val = val_min
        else:
            val += val_step


def imit_rand(
    cnt_time: int = 100000,
    cnt_id: int = 100,
    list_id: Optional[List[int]] = None,
    type_pack: int = 0,
    f_time: Optional[float] = None,
    sleep_s: Optional[float] = None,
    max_events_per_sec: Optional[float] = None,
) -> Iterator[Tuple[float, int, float, int]] | Iterator[bytes]:
    """Generate random signal values for simulation.

    Creates random values for specified signals with configurable rate limiting.
    Useful for load testing and simulating spontaneous events.

    Args:
        cnt_time: Number of time iterations (each iteration generates cnt_id events).
        cnt_id: Number of signals per iteration.
        list_id: List of signal IDs to choose from randomly.
        type_pack: Output format:
            0 = tuple (timestamp, id, value, quality)
            1 = packed bytes using format '<dLdH' (double, unsigned long, double, unsigned short)
            2 = packed bytes using format '<dLfB' (double, unsigned long, float, unsigned char)
        f_time: Start time (None = current time).
        sleep_s: Sleep duration between iterations (None = SIM_SLEEP).
                 Sleep occurs only every SIM_SLEEP_EVERY_N-th packet.
        max_events_per_sec: Maximum events per second (None = no limit).
                            Reduces CPU under high load.

    Yields:
        Depending on type_pack:
            - 0: Tuple of (timestamp, signal_id, value, quality)
            - 1 or 2: Packed bytes structure

    Example:
        >>> # Generate 100 random events with values 4.0-6.0
        >>> for ts, sid, val, q in imit_rand(cnt_time=10, cnt_id=10,
        ...                                   list_id=[1, 2, 3]):
        ...     print(f"ID:{sid} VAL:{val:.2f} Q:{q}")
        ID:2 VAL:5.23 Q:0
        ID:1 VAL:4.89 Q:1
        ...

        >>> # Generate packed bytes for network transmission
        >>> for packet in imit_rand(cnt_time=5, cnt_id=5,
        ...                         list_id=[1, 2], type_pack=2):
        ...     print(packet.hex())
    """
    if list_id is None:
        list_id = []

    # Configure sleep behavior
    if sleep_s is None:
        sleep_s = SIM_SLEEP

    # Rate limiting
    rate_sleep = (1.0 / max_events_per_sec) if max_events_per_sec and max_events_per_sec > 0 else 0.0

    # Initialize time
    f_time = time.time() if f_time is None else f_time

    len_id = len(list_id)
    random_random = random.random

    # Pre-compile packers for better performance
    pack_1 = struct.Struct('<dLdH').pack  # double, unsigned long, double, unsigned short
    pack_2 = struct.Struct('<dLfB').pack  # double, unsigned long, float, unsigned char

    for tme in range(cnt_time):
        base_time = tme + f_time

        for _ in range(cnt_id):
            rnd = random_random()
            sid = list_id[int(rnd * len_id)]
            val = rnd * 2.0 + 4.0  # Values in range 4.0 - 6.0
            ts = base_time + rnd
            q = 1 if rnd > 0.9 else 0  # ~10% invalid quality

            if type_pack == 0:
                yield ts, sid, val, q
            elif type_pack == 1:
                yield pack_1(ts, sid, val, q)
            elif type_pack == 2:
                yield pack_2(ts, sid, val, q)

        # Sleep to reduce CPU load (only every N-th iteration)
        if sleep_s > 0 and tme % SIM_SLEEP_EVERY_N == 0:
            time.sleep(sleep_s)


if __name__ == '__main__':
    # Test ladder generator
    print("=== Ladder Generator Test ===")
    for v in imit_ladder(
        cnt_step=10,
        time_step=0.1,
        val_step=1,
        val_min=0,
        val_max=5,
        list_id=[1, 2]
    ):
        print(v)

    # Test random generator
    print("\n=== Random Generator Test ===")
    for v in imit_rand(
        cnt_time=0,
        cnt_id=10,
        list_id=[1, 2]
    ):
        print(v)