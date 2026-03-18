import time

for _ in range(10):
    print(time.time(), time.monotonic())
    time.sleep(1)
