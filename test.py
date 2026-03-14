import time
import logging

# Настройка логирования на INFO (DEBUG не будет писаться)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('test')

def test_logging_overhead():
    event = ('test', 123, 1, 456, 789.123)
    state = type('State', (), {'send_sq': 42})()
    
    # Тест 1: с вызовом log.debug (но уровень INFO)
    start = time.perf_counter()
    for i in range(100000):
        log.debug(f"S->C [I-FRAME] IOA:{event[1]} VAL:{event[3]} TIME:{event[4]} V(S):{state.send_sq}")
    debug_time = time.perf_counter() - start
    
    # Тест 2: без вызова log.debug
    start = time.perf_counter()
    for i in range(100000):
        pass
    empty_time = time.perf_counter() - start
    
    # Тест 3: вычисление строки без логирования
    start = time.perf_counter()
    for i in range(100000):
        msg = f"S->C [I-FRAME] IOA:{event[1]} VAL:{event[3]} TIME:{event[4]} V(S):{1}"
    format_time = time.perf_counter() - start
    
    print(f"100,000 итераций:")
    print(f"  Пустой цикл: {empty_time*1000:.2f} мс")
    print(f"  Только форматирование: {format_time*1000:.2f} мс")
    print(f"  С log.debug (уровень INFO): {debug_time*1000:.2f} мс")
    print(f"  Накладные расходы на log.debug: {(debug_time-format_time)*1000:.2f} мс")
    print(f"  Время на один вызов: {(debug_time)/100000*1000000:.2f} мкс")

test_logging_overhead()