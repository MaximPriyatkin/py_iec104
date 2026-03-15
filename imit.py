import random
import time
from typing import Iterator, Tuple
import struct

# Пауза (с) после каждого N-го yield — снижение нагрузки CPU
SIM_SLEEP = 0.1
SIM_SLEEP_EVERY_N = 10  # спать только каждый N-й пакет


def sim_float(cnt_time: int = 100000,
              cnt_id: int = 100,
              list_id: list = list(),
              type_pack: int = 0,
              f_time: float | None = None,
              sleep_s: float | None = None,
              max_events_per_sec: float | None = None,
              ) -> Iterator[Tuple[float, int, float, int]] | Iterator[bytes]:
    """Генератор значений для имитации ТИ.

    Args:
        cnt_time: количество повторов.
        cnt_id: количество сигналов в повторе.
        list_id: список id сигналов.
        type_pack: 0=tuple, 1/2=bytes.
        f_time: начальное время.
        sleep_s: пауза (с); None = SIM_SLEEP. Срабатывает каждый SIM_SLEEP_EVERY_N-й пакет (cnt % N == 0).
        max_events_per_sec: макс. событий/с (None = без лимита). Снижает CPU при большой нагрузке.
    """
    if sleep_s is None:
        sleep_s = SIM_SLEEP
    rate_sleep = (1.0 / max_events_per_sec) if max_events_per_sec and max_events_per_sec > 0 else 0.0
    f_time = time.time() if f_time is None else f_time
    len_id = len(list_id)
    random_random = random.random
    pack_1 = struct.Struct('<dLdH').pack
    pack_2 = struct.Struct('<dLfB').pack
    for tme in range(cnt_time):
        base_time = tme + f_time
        for _ in range(cnt_id):
            rnd = random_random()
            id = list_id[int(rnd * len_id)]
            val = rnd * 2.0 + 4.0
            tme_sg = base_time + rnd
            q = 1 if rnd > 0.9 else 0
            if type_pack == 0:
                yield (tme_sg, id, val, q)
            elif type_pack == 1:
                yield pack_1(tme_sg, id, val, q)
            elif type_pack == 2:
                yield pack_2(tme_sg, id, val, q)
        if sleep_s > 0 and tme % SIM_SLEEP_EVERY_N == 0:
            time.sleep(sleep_s)

if __name__ == '__main__':
    for v in sim_float(cnt_time=10, cnt_id=10, list_id=[1,2]):
        print(v)