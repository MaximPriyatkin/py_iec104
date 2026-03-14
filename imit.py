import random
import time
from typing import Iterator, Tuple
import struct

def sim_float(cnt_time: int = 100000, 
              cnt_id: int = 100,
              list_id: list = list(),
              type_pack: int = 0,
              f_time: float | None = None
              ) -> Iterator[Tuple[float, int, float, int]] | Iterator[bytes]:
    """_summary_

    Args:
        cnt_time (int, optional): Количество повторов. Defaults to 100000.
        cnt_id (int, optional): Количество сигналов в повторе. Defaults to 100.
        f_time (float, None): Начальное время.

    Yields:
        Iterator[Tuple[float, int, float, int]]: _description_
    """    
    f_time = time.time() if f_time is None else f_time
    len_id =  len(list_id)
    random_random = random.random
    pack_1 = struct.Struct('<dLdH').pack
    pack_2 = struct.Struct('<dLfB').pack
    for tme in range (cnt_time):
        base_time = tme + f_time
        for _ in range(cnt_id):
            rnd = random_random()
            id = list_id[int(rnd * len_id)]
            val = rnd * 2.0 + 4.0
            tme_sg = base_time + rnd
            q = 0
            if rnd > 0.9:
                q = 1
            if type_pack == 0:
                yield (tme_sg, id, val,q)
            if type_pack == 1:
                yield pack_1(tme_sg, id, val, q)
            if type_pack == 2:
                yield pack_2(tme_sg, id, val, q)

if __name__ == '__main__':
    for v in sim_float(cnt_time=10, cnt_id=10, list_id=[1,2]):
        print(v)