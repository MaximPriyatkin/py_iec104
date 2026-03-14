import time
import os
import sys
import common as cm

conf = cm.load_config()

def watch_logs(fname: str) -> int:
    if not os.path.exists(fname):
        print(f'Нет лог-файла {fname}')
        return 0
    print(f'--- Ожидание записей в {fname} (Ctrl+C для выхода) ---')
    with open(fname, 'r', encoding='utf-8') as f:
        f.seek(0, os.SEEK_END)
        try:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(1)
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
        except KeyboardInterrupt:
            print('\n Просмотр лог-файла завершен')
            return 1
    
if __name__ == '__main__':
    fname = sys.argv[1] if len(sys.argv) > 1 else conf.log_fname
    watch_logs(fname)