import time


class TimeProfiler:
    def __init__(self):
        self.__checkpoints = []

    def start(self):
        self.__checkpoints = [("Start", 0, time.time())]

    def checkpoint(self, name:str) -> None:
        now = time.time()
        prev = self.__checkpoints[-1][2]
        self.__checkpoints.append((name, now - prev, now))

    def __str__(self):
        summary = "\n\t".join([f"{name}: {1000*dt}" for name, dt, abs_t in self.__checkpoints])
        total_dt = self.__checkpoints[-1][2] - self.__checkpoints[0][2]
        return f"checkpoints (ms):\n{summary}\n\tTotal: {1000*total_dt}"