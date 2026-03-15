import socket
from types import SimpleNamespace
from typing import Callable
from threading import Thread, Event
import queue
import time
# ------------------------------
import const
import common as cm
import protocol as prt
import imit as im

def client_send(state: cm.ClientState):
    assert state.log is not None
    assert state.out_que is not None
    assert state.conn is not None
    log = state.log
    log.info('Поток отправки запущен')
    t = time.time()
    while not state.stop_event.is_set():
        try:
            try:
                event = state.out_que.get(timeout=1.0)
                if not state.startdt_confirmed:
                    log.debug(f'Передача запрещена, событие {event} игнорируем')
                    continue
                unack = (state.send_sq - state.last_ack_nr) % 32768
                if unack >= state.conf.prot_k:
                    state.out_que.put(event)
                    time.sleep(0.01) 
                    continue

                packet = prt.build_i_frame(state, event)
                if packet is not None:
                    state.conn.send(packet)               
                state.last_send = time.time()
                state.send_sq = (state.send_sq + 1) % 32768
                if state.conf.send_sleep > 0:
                    time.sleep(state.conf.send_sleep)
                n = state.conf.log_i_frame_stats_every
                if n and state.send_sq % n == 0 and time.time() - t > 0:
                    dt = time.time() - t
                    log.info(f"I-frame: отправлено {state.send_sq}, очередь {state.out_que.qsize()}, скорость {n/dt:.1f} пак/с")
                    t = time.time()
                log.debug(f"S->C [I-FRAME] IOA:{event.ioa} VAL:{event.val} TIME:{event.ts} V(S):{state.send_sq}")
            except queue.Empty:
                pass
            now = time.time()
            if state.conf and (now - state.last_send) >= state.conf.prot_t3:
                log.debug(f'S->C [TESTFR ACT] Канала простаивает {state.conf.prot_t3}c')
                state.conn.send(const.TESTFR_ACT)
                state.last_send = now
        except (socket.error, ConnectionError, BrokenPipeError) as e:
            log.error(f"Ошибка записи в сокет {e}")
            state.stop_event.set()
            break
    log.debug('Поток отправки остановлен')

def client_rec(state, remove_client, data_storage):
    log = state.log
    buffer = bytearray()
    state.conn.settimeout(1.0)
    with state.conn:
        try:
            while not state.stop_event.is_set():
                try:
                    data = state.conn.recv(1024)
                except socket.timeout:
                    continue
                if not data:
                     break
                log.debug(f'С->S [RAW] {data.hex(" ").upper()}')
                buffer.extend(data)
                if len(buffer) > state.conf.max_rx_buf:
                    log.error(f'Буфер переполнен, очищаем буфер {len(buffer)} > {state.conf.max_rx_buf}')
                    state.stop_event.set()
                    break
                while len(buffer) >= 6:
                    try:
                        start_idx = buffer.index(0x68)
                    except ValueError:
                        log.warning('В буфере нет стартового байта 0x68, очищаем буфер')
                        buffer.clear()
                        break
                    if start_idx > 0:
                        log.warning(f'Пропускаем {start_idx} байт(а) до стартового байта 0x68')
                        del buffer[:start_idx]
                    if len(buffer) < 2:
                        break
                    apdu_len = buffer[1]
                    total_frame_len = apdu_len + 2
                    if len(buffer) < total_frame_len:
                        log.debug(f'Ждем данные, сейчас {len(buffer)}, нужно {total_frame_len}')
                        break
                    frame = buffer[:total_frame_len]
                    del buffer[:total_frame_len]
                    f_type, response = prt.proc_frame(frame, state)
                    if f_type == 'I':
                        state.rec_count_since_send += 1
                    if response:
                        state.conn.send(response)
                        state.last_send = time.time()
                        state.rec_count_since_send = 0
                        if f_type == 'I' and frame[6] == const.AsduTypeId.C_IC_NA_1:
                            pass  # обработка ответа на общий опрос
                        log.debug(f'S->C [{f_type}-CON] {response.hex(" ").upper()}')
                    elif f_type == 'I' and state.rec_count_since_send >= state.conf.prot_w:
                        state.conn.send(prt.build_s_frame(state))
                        state.last_send = time.time()
                        state.rec_count_since_send = 0
                        log.debug(f'S->C [S-FRAME] N(R)={state.rec_sq}')
        except (ConnectionError, BrokenPipeError, socket.error):
            state.stop_event.set()
        finally:
            data_storage.unsubscribe(state.addr)
            remove_client(state.addr)    
    log.info(f'Отключился клиент {state.addr}')


def _cmd_exit(ctx, _args):
    ctx.log.info('Останавливаем сервер')
    ctx.stop_thread.set()
    return True

def _cmd_clients(ctx, _args):
    for addr, state in ctx.cl.get_clients().items():
        print(addr, state)

def _cmd_addr(ctx, _args):
    cm.print_signals(ctx.sg.get_all())

def _cmd_set(ctx, args):
    res = ctx.sg.update_val(float(args[0]), id=int(args[1]))
    if res:
        cm.print_signals(ctx.sg.get_all())

def _cmd_setioa(ctx, args):
    res = ctx.sg.update_val(float(args[0]), ioa=int(args[1]))
    if res:
        cm.print_signals(ctx.sg.get_all())

def _cmd_imit(ctx, args):
    cnt_time, cnt_id = int(args[0]), int(args[1])
    def run():
        for _, sid, val, q in im.sim_float(cnt_time=cnt_time, cnt_id=cnt_id, list_id=[5, 6], sleep_s=im.SIM_SLEEP):
            ctx.sg.update_val(val, id=sid, new_q=q)
        ctx.log.info('Имитация завершена')
    Thread(target=run, daemon=True).start()
    print('Имитация запущена в фоне')

def _cmd_help(ctx, _args):
    for name, (n, _) in COMMANDS.items():
        print(f"  {name}" + (f" <arg1> <arg2>" if n else ""))

# (число аргументов, обработчик; обработчик возвращает True только для exit)
COMMANDS = {
    "exit": (0, _cmd_exit),
    "clients": (0, _cmd_clients),
    "addr": (0, _cmd_addr),
    "set": (2, _cmd_set),
    "setioa": (2, _cmd_setioa),
    "imit": (2, _cmd_imit),
    "help": (0, _cmd_help),
}

def server_handler(stop_thread: Callable, cl: Callable, sg: Callable, log):
    """Обработка пользовательского ввода: реестр команд, единый цикл разбора."""
    ctx = SimpleNamespace(stop_thread=stop_thread, cl=cl, sg=sg, log=log)
    while not stop_thread.is_set():
        try:
            line = input('> ').strip().lower()
        except EOFError:
            log.info('Ввод закрыт, останавливаем сервер')
            stop_thread.set()
            return
        except Exception as e:
            log.exception('Ошибка ввода: %s', e)
            continue
        if not line:
            continue
        parts = line.split()
        cmd_name, args = parts[0], parts[1:]
        entry = COMMANDS.get(cmd_name)
        if entry is None:
            log.info('Команда не распознана: %s', cmd_name)
            print('Команда не распознана. help — список команд.')
            continue
        n_args, handler = entry
        if len(args) != n_args:
            print(f'Ожидается {n_args} арг. для {cmd_name}, получено {len(args)}. help — список команд.')
            continue
        try:
            if handler(ctx, args):
                return
        except Exception as e:
            log.exception('Ошибка выполнения команды %s: %s', cmd_name, e)
            print('Ошибка:', e)

def main(conf:cm.Conf):
    client_storage = cm.create_client_storage()
    data_storage = cm.create_data_storage()
    ca = int(conf.prot_ca)
    cm.load_signal(data_storage.add_signal, ca)
    stop_thread = Event()
    client_threads = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((conf.nw_bind_ip, conf.nw_port))
        sock.listen()
        sock.settimeout(1.0)
        log.info(f'Запущен сервер порт: {conf.nw_port}')
    except OSError as e:
        print(f'ошибка при создании сокета {e}')
        return
    # Старт потока управления сервером
    Thread(
        target=server_handler,
        args=(stop_thread, client_storage, data_storage, log),
        daemon=True).start()
    try:
        while not stop_thread.is_set():
            try:
                conn, addr = sock.accept() 
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                if addr[0] not in conf.nw_allow_ip:
                    log.warning(f'Клиент {addr} не в списке разрешенных IP')
                    conn.close()
                    continue    
                client_threads = [t for t in client_threads if t.is_alive()]
                log.info(f'Обслуживается потоков: {len(client_threads)}')
                log.info(f'Подключен клиент {addr}')
                state = cm.ClientState()
                state.ca = ca
                state.conn = conn
                state.addr = addr
                state.conf = conf
                state.log = cm.logging.getLogger(f'{conf.log_name}.{addr[0]}:{addr[1]}')
                state.out_que = queue.Queue()
                state.on_command = lambda val, ioa: data_storage.update_val(val, ioa=ioa)
                state.on_gi = data_storage.get_all_for_gi
                data_storage.subscribe(addr, state.out_que)
                client_storage.add_client(state)
                # Старт отправки данных 
                t = Thread(
                    target=client_send,
                    args=(state, ),
                    daemon=True)
                t.start()
                client_threads.append(t)
                # Старт потока чтения данных от сервера
                t = Thread(
                    target=client_rec,
                    args=(state, client_storage.remove_client, data_storage))
                t.start()
                client_threads.append(t)

            except socket.timeout:
                continue
    except KeyboardInterrupt:
        log.warning('cервер остановлен по ctrl-c')
    finally:
        stop_thread.set()
        client_storage.close_all()
        log.info('Ожидание завершения клиентских потоков')
        client_storage.close_all()
        for t in client_threads:
            t.join(timeout=2.0)
        sock.close()
        log.info('Сервер остановлен')

if __name__ == '__main__':
    conf = cm.load_config()
    log = cm.setup_logging(conf)
    main(conf)