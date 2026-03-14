import socket
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
                packet = prt.build_i_frame(state, event)
                if packet is not None:
                    state.conn.send(packet)
                state.last_send = time.time()
                state.send_sq = (state.send_sq + 1) % 32768
                
                if state.send_sq % 100 == 0 and time.time() - t > 0:    
                    log.info(f"{state.send_sq} пакетов отправлено {100 / (time.time() - t)} пак/сек")
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
                    if response:
                        state.conn.send(response)
                        state.last_send = time.time()
                        if f_type == 'I' and frame[6] == const.AsduTypeId.C_IC_NA_1:
                            pass  # обработка ответа на общий опрос
                        log.debug(f'S->C [{f_type}-CON] {response.hex(" ").upper()}')                       
        except (ConnectionError, BrokenPipeError, socket.error):
            state.stop_event.set()
        finally:
            data_storage.unsubscribe(state.addr)
            remove_client(state.addr)    
    log.info(f'Отключился клиент {state.addr}')


def server_handler(stop_thread: Callable, cl: Callable, sg: Callable, log):
    """обработка пользовательского ввода для управления отправкой данных
    Args:
        stop_thread (Callable): событие для остановки потока
        cl (Callable): хранилище клиентов
        sg (Callable): хранилище сигналов
        log: логгер сервера
    """
    while not stop_thread.is_set():
        cmd = input('> ').strip().lower()
        spl_cmd = cmd.split(' ')
        if cmd == '':
            continue
        elif cmd == 'exit':
            log.info('Останавливаем сервер')
            stop_thread.set()
            return
        elif cmd == 'clients':
            for addr, state in cl.get_clients().items():
                print(addr, state)
        elif cmd == 'addr':
            cm.print_signals(sg.get_all())
                
        if len(spl_cmd) == 3:
            if spl_cmd[0] == 'set':
                res = sg.update_val(int(spl_cmd[1]), spl_cmd[2])
                if res:
                    cm.print_signals(sg.get_all())
            elif spl_cmd[0] == 'imit':
                for v in im.sim_float(cnt_time=int(spl_cmd[1]), cnt_id=int(spl_cmd[2]), list_id=[5]):
                    sg.update_val(v[1], v[2])
            else:
                print('Ошибка ввода')
        elif cmd == 'help':
            print(
            '''
            exit - выход
            clients - список клиентов
            '''
            )
        else:
            log.info('Команда не распознана')

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