from socket import socket, AF_INET, SOCK_STREAM, timeout as socket_timeout
from threading import Thread, Timer, Event

def client_hdl(sock):
    while True:
        cmd = input('> ')
        sock.send(cmd.encode('utf-8'))

def client_replay(sock, msg):
    print(f"Получено: {msg.decode('utf-8', errors='ignore')}")
    sock.send(b'success')

def main():
    stop_thread = Event()
    sock = None
    client_threads = []

    try:
        sock = socket(AF_INET, SOCK_STREAM)
        sock.connect(('localhost', 3051))
        sock.settimeout(1.0)
        print('выполнено подключение')
        sock.send(b'hello')
        t = Thread(
        target=client_hdl,
        args=(sock,)
        )
        t.start()
        client_threads.append(t)
        while not stop_thread.is_set():    
            try:
                msg = sock.recv(1024)
                if not msg:
                    print('сервер закрыл соединение')
                    break
                client_replay(sock, msg)
            except socket_timeout:
                continue
            except ConnectionResetError:
                print('соединение сброшено сервером')
                break
            except ConnectionAbortedError:
                print('соединение прервано')
                break
            except ConnectionError as e:
                print(f'ошибка соединения {e}')
                break
            except KeyboardInterrupt:
                print('получен сигнал остановки')
                break
            except Exception as e:
                print(f'ошибка {e}')
                continue
    except ConnectionRefusedError:
        print('сервер недоступен')
    except KeyboardInterrupt:
        print('\nклиент останавливается по ctrl-с')
    except Exception as e:
        print(f'не удалось выполнить подключение \n{e}')
    finally:
        stop_thread.set()
        sock.close()
        print('Ожидание завершения клиентских потоков...')
        for t in client_threads:
            t.join(timeout=2.0)
        print('клиент остановлен')

if __name__ == '__main__':
    main()