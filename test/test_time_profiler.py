import time

import pytest

from  picframe.time_profiler import TimeProfiler

def test_timer()-> None:
    timer = TimeProfiler()

    timer.start()
    time.sleep(0.5)
    timer.checkpoint("sleep-0.5")
    time.sleep(1.0)
    timer.checkpoint("sleep-1.0")
    timer.checkpoint("no sleep")

    print(f"Test {timer}")