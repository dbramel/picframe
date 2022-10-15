from random import random
from typing import Protocol, Tuple

from pi3d import Sprite

import attr

class SlideMutator(Protocol):
    def mutate(self, slide:Sprite, t:float) -> None:
        """ Note that time t goes from 0(start) to 1(finish) """
        ...

class RandomizerMixin(Protocol):
    def randomize(self) -> None:
        ...

class TextureXYShifter(SlideMutator, Protocol):
    def mutate(self, slide:Sprite, t:float) -> None:
        next_x, next_y = self.compute_xy(slide.unif[48], slide.unif[49], t)
        slide.unif[48] = next_x
        slide.unif[49] = next_y

    def compute_xy(self, old_x: float, old_y: float, t:float) -> Tuple[float, float]:
        ...

@attr.define
class LinearTextureShifter(TextureXYShifter, RandomizerMixin):
    scale:float
    x_rate:float = attr.ib(init=False)
    y_rate:float = attr.ib(init=False)
    last_t:float = 0

    def compute_xy(self, old_x: float, old_y: float, t:float) -> Tuple[float, float]:
        dt = t-self.last_t
        last_t = t
        return old_x + dt * self.x_rate, old_y + dt * self.y_rate

    def randomize(self) -> None:
        self.x_rate = self.scale * random()
        self.y_rate = self.scale * random()
        last_t = 0