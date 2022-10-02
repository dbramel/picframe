import time


class TimeProfiler:
    def __init__(self):
        self.__checkpoints = []

    def start(self):
        self.checkpoint("Start")

    def checkpoint(self, name:str) -> None:
        now = time.time()
        prev = self.__checkpoints[-1][1]
        self.__checkpoints.append(name, now - prev)

    def reset(self):
        self.__checkpoints = []

    def __str__(self):
        t = self.__start_time
        summary = "\n\t".join([f"{name}: {dt}" for name, dt in self.__checkpoints])

        return f"checkpoints:\n"