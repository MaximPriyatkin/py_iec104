"""
Server implementation for IEC 104 protocol simulator.

This module implements the server side of IEC 104 protocol, simulating a field
device (RTU/PLC) that accepts connections from SCADA clients. It handles
multiple client connections concurrently, manages signal values, and provides
data simulation capabilities.

The server supports:
- Multiple concurrent client connections
- Signal value management with quality indicators
- Data simulation (random and ladder patterns)
- Priority queues for outgoing messages
- Full IEC 104 protocol implementation (I, S, U frames)
"""

import socket
from threading import Thread, Event
import queue
import time
# ------------------------------
import const
import common as cm
import protocol as prt
import imit as im
from control_server import server_handler

# state in client_send/client_rec is always initialized
# pyright: reportOptionalMemberAccess=false


def client_send(state: cm.ClientState) -> None:
    """Send loop for transmitting data to the client.

    This function runs in a dedicated thread for each connected client,
    continuously fetching events from the outbound queue and sending them
    as IEC 104 I-frames. It implements:

    - STARTDT verification before sending data
    - Flow control (k-value window management)
    - Batch packing of multiple ASDUs into a single I-frame
    - Keepalive (TESTFR) for idle channels
    - Transmission statistics logging

    Args:
        state: Client state object containing queue, socket, and protocol state.

    Note:
        The function exits when stop_event is set or connection fails.
    """
    log = state.log
    log.info('Send thread started')
    get_time = time.monotonic
    t = get_time()
    while not state.stop_event.is_set():
        try:  # connection handling
            try:  # frame queue handling
                event = state.out_que.get(timeout=1.0)
                if not state.startdt_confirmed:
                    log.debug(f'Transmission disabled, event {event} ignored')
                    time.sleep(0.01)  # not critical, keep sleep
                    continue
                with state.seq_lock:
                    unack = (state.send_sq - state.last_ack_nr) % 32768
                if unack >= state.conf.prot_k:
                    # Transmission blocked, waiting for client acknowledgment
                    state.out_que.put(event)
                    time.sleep(0.01)  # todo - add event from receive frame
                    continue
                batch = [event]  # pack multiple ASDUs into one frame for faster transmission
                obj_size = 3 + const.ASDU_DATA_SIZE.get(event.asdu, 1)  # - 3 bytes ASDU size
                # 243 - max objects in APDU to fit regardless of ASDU type
                # 127 - maximum number of objects
                max_obj = min(127, 243 // obj_size)
                # if consecutive items in queue have same ASDU - pack them into one packet
                try:
                    while len(batch) < max_obj:
                        nxt = state.out_que.get_nowait()
                        if nxt.asdu == event.asdu and nxt.cot == event.cot:
                            batch.append(nxt)
                        else:
                            state.out_que.put(nxt)
                            break
                except queue.Empty:
                    pass
                with state.seq_lock:
                    packet = prt.build_i_frame(state, batch)
                    if packet is None:
                        # Retries are pointless: protocol doesn't support at least one ASDU in batch.
                        log.warning(f"S->C Failed to build I-frame ASDU {batch[0].asdu} (unsupported type)")
                        continue
                    with state.sock_lock:
                        state.conn.send(packet)
                    now = get_time()
                    state.last_send = now  # for idle channel TESTFR generation
                    state.send_sq = (state.send_sq + 1) % 32768  # 32768 - protection against 2-byte field overflow
                    state.sent_obj += len(batch)  # I-frame statistics
                    n = state.conf.log_i_frame_stats_every  # interval for statistics logging
                    # Log transmission statistics
                    if n and state.sent_obj % n < len(batch) and get_time() - t > 0:
                        dt = get_time() - t
                        log.info(
                            f"I-frame: frames {state.send_sq}, objects {state.sent_obj}, queue {state.out_que.qsize()}, rate {state.sent_obj/dt:.1f} obj/s"
                        )
                        t = get_time()
                        state.sent_obj = 0
                    log.debug(f"S->C [I-FRAME] ASDU:{event.asdu} COUNT_OBJ:{len(batch)} V(S):{state.send_sq}")
            except queue.Empty:
                pass
            now = get_time()
            if state.conf:
                send_test = False
                with state.seq_lock:
                    if (now - state.last_send) >= state.conf.prot_t3:
                        state.last_send = now
                        send_test = True
                if send_test:
                    log.debug(f'S->C [TESTFR ACT] Channel idle for {state.conf.prot_t3}s')
                    with state.sock_lock:
                        state.conn.send(const.TESTFR_ACT)
        except (socket.error, ConnectionError, BrokenPipeError) as e:
            log.error(f"Socket write error {e}")
            state.stop_event.set()
            break
    log.debug('Send thread stopped')


def client_rec(state, remove_client, data_storage) -> None:
    """Receive loop for processing incoming data from the client.

    This function runs in a dedicated thread for each connected client,
    continuously receiving data from the socket, buffering it, and processing
    complete IEC 104 frames. It handles:

    - Frame buffering and reconstruction
    - Frame type detection and routing
    - S-frame generation for window management
    - Command processing (e.g., general interrogation)
    - Connection cleanup on disconnection

    Args:
        state: Client state object containing socket, buffer, and protocol state.
        remove_client: Callback to remove client from storage on disconnect.
        data_storage: Storage for updating signal values from commands.

    Note:
        The function exits when stop_event is set or the connection is closed.
    """
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
                log.debug(f'C->S [RAW] {data.hex(" ").upper()}')
                buffer.extend(data)
                if len(buffer) > state.conf.max_rx_buf:
                    log.error(f'Buffer overflow, clearing buffer {len(buffer)} > {state.conf.max_rx_buf}')
                    state.stop_event.set()
                    break
                while len(buffer) >= 6:
                    try:
                        start_idx = buffer.index(0x68)
                    except ValueError:
                        log.warning('No start byte 0x68 in buffer, clearing buffer')
                        buffer.clear()
                        break
                    if start_idx > 0:
                        log.warning(f'Skipping {start_idx} byte(s) before start byte 0x68')
                        del buffer[:start_idx]
                    if len(buffer) < 2:
                        break
                    apdu_len = buffer[1]
                    total_frame_len = apdu_len + 2
                    if len(buffer) < total_frame_len:
                        log.debug(f'Waiting for data, currently {len(buffer)}, need {total_frame_len}')
                        break
                    frame = buffer[:total_frame_len]
                    del buffer[:total_frame_len]
                    with state.seq_lock:
                        f_type, response = prt.proc_frame(frame, state)
                        if f_type == 'I':
                            state.rec_count_since_send += 1
                        if response:
                            # Important: send response under seq_lock
                            # to prevent other thread from changing V(S) between response building and sending
                            with state.sock_lock:
                                state.conn.send(response)
                            state.last_send = time.monotonic()  # for accurate diagnostic interval calculation
                            state.rec_count_since_send = 0
                            if not (f_type == 'I' and frame[6] == const.AsduTypeId.C_IC_NA_1):
                                log.debug(f'S->C [{f_type}-CON] {response.hex(" ").upper()}')
                        elif f_type == 'I' and state.rec_count_since_send >= state.conf.prot_w:
                            s_frame = prt.build_s_frame(state)
                            with state.sock_lock:
                                state.conn.send(s_frame)
                            state.last_send = time.monotonic()
                            state.rec_count_since_send = 0
                            log.debug(f'S->C [S-FRAME] N(R)={state.rec_sq}')
        except (ConnectionError, BrokenPipeError, socket.error):
            state.stop_event.set()
        finally:
            data_storage.unsubscribe(state.addr)
            remove_client(state.addr)
    log.info(f'Client disconnected {state.addr}')


def create_server_socket(conf: cm.Conf) -> socket.socket:
    """Create and configure the server socket.

    Creates a TCP socket with SO_REUSEADDR to allow immediate reuse of
    the port after the server stops, binds to the configured address and
    port, and sets a timeout for accept() calls.

    Args:
        conf: Configuration object containing network settings.

    Returns:
        Configured and listening socket.

    Raises:
        OSError: If socket creation or binding fails.

    Example:
        >>> sock = create_server_socket(conf)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((conf.nw_bind_ip, conf.nw_port))
    sock.listen()
    sock.settimeout(1.0)
    return sock


def is_client_allowed(conf: cm.Conf, addr) -> bool:
    """Check if a client IP is allowed to connect.

    Args:
        conf: Configuration object containing allowed IP list.
        addr: Client address tuple (ip, port).

    Returns:
        True if the client IP is in the allowed list, False otherwise.

    Example:
        >>> if is_client_allowed(conf, ("192.168.1.100", 2404)):
        ...     accept_connection()
    """
    return addr[0] in conf.nw_allow_ip


def create_client_state(conn, addr, conf: cm.Conf, ca: int, data_storage) -> cm.ClientState:
    """Create and initialize a client state object for a new connection.

    Args:
        conn: Connected socket object.
        addr: Client address tuple (ip, port).
        conf: Global configuration object.
        ca: Common Address for this client.
        data_storage: Storage for updating signals from commands.

    Returns:
        Initialized ClientState object.

    Example:
        >>> state = create_client_state(conn, ("192.168.1.100", 2404), conf, 1, storage)
    """
    state = cm.ClientState()
    state.ca = ca
    state.conn = conn
    state.addr = addr
    state.conf = conf
    state.log = cm.logging.getLogger(f'{conf.log_name}.{addr[0]}:{addr[1]}')
    state.out_que = queue.Queue()
    state.on_command = lambda val, ioa: data_storage.update_val(val, ioa=ioa)
    state.on_gi = data_storage.get_all_for_gi
    return state


def start_client_threads(state, client_storage, data_storage) -> list:
    """Start send and receive threads for a client.

    Creates and starts two daemon threads:
    - client_send: Handles outgoing data transmission
    - client_rec: Handles incoming data reception

    Args:
        state: Client state object for the session.
        client_storage: Storage for managing client states.
        data_storage: Storage for signal data.

    Returns:
        List of started threads.

    Example:
        >>> threads = start_client_threads(state, storage, data_storage)
    """
    threads = []
    # Start data transmission thread
    t = Thread(
        target=client_send,
        args=(state,),
        daemon=True)
    t.start()
    threads.append(t)
    # Start data reception thread
    t = Thread(
        target=client_rec,
        args=(state, client_storage.remove_client, data_storage),
        daemon=True)
    t.start()
    threads.append(t)
    return threads


def cleanup_dead_threads(client_threads: list) -> list:
    """Remove terminated threads from the thread list.

    Args:
        client_threads: List of client threads.

    Returns:
        Filtered list containing only alive threads.

    Example:
        >>> client_threads = cleanup_dead_threads(client_threads)
    """
    return [t for t in client_threads if t.is_alive()]


def run_accept_loop(
    sock: socket.socket,
    stop_thread: Event,
    conf: cm.Conf,
    ca: int,
    log,
    client_storage,
    data_storage,
    client_threads: list,
) -> None:
    """Main accept loop for incoming client connections.

    Continuously accepts new client connections, validates IP addresses,
    creates client state, and starts communication threads for each client.

    Args:
        sock: Listening socket.
        stop_thread: Event to signal when the server should stop.
        conf: Configuration object.
        ca: Common Address for clients.
        log: Logger for server events.
        client_storage: Storage for client states.
        data_storage: Storage for signal data.
        client_threads: List to track client threads (modified in place).

    Note:
        The loop runs until stop_thread is set.
    """
    while not stop_thread.is_set():
        try:
            conn, addr = sock.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if not is_client_allowed(conf, addr):
                log.warning(f'Client {addr} not in allowed IP list')
                conn.close()
                continue
            client_threads[:] = cleanup_dead_threads(client_threads)
            log.info(f'Active threads: {len(client_threads)}')
            log.info(f'Client connected {addr}')
            state = create_client_state(conn, addr, conf, ca, data_storage)
            data_storage.subscribe(addr, state.out_que)
            client_storage.add_client(state)
            client_threads.extend(start_client_threads(state, client_storage, data_storage))
        except socket.timeout:
            continue


def shutdown_server(
    stop_thread: Event,
    client_storage,
    client_threads: list,
    sock: socket.socket,
    log,
) -> None:
    """Gracefully shut down the server and clean up resources.

    Sets the stop event, closes all client connections, waits for threads
    to finish, and closes the server socket.

    Args:
        stop_thread: Event to signal threads to stop.
        client_storage: Storage for client states.
        client_threads: List of client threads to join.
        sock: Server socket to close.
        log: Logger for shutdown messages.
    """
    stop_thread.set()
    client_storage.close_all()
    log.info('Waiting for client threads to finish')
    for t in client_threads:
        t.join(timeout=2.0)
    sock.close()
    log.info('Server stopped')


def main() -> None:
    """Main entry point for the server application.

    Initializes configuration and logging, creates data and client storage,
    loads signals from configuration, creates the server socket, starts the
    command handler thread, and runs the main accept loop.

    The server will run until interrupted by Ctrl+C or until the stop event
    is set via the command handler.

    Example:
        >>> python server.py
        > Server KP_1> help
    """
    conf = cm.load_config()
    log = cm.setup_logging(conf)
    client_storage = cm.create_client_storage()
    data_storage = cm.create_data_storage()
    ca = int(conf.prot_ca)
    cm.load_signal(data_storage.add_signal, ca)
    stop_thread = Event()
    client_threads = []
    try:
        sock = create_server_socket(conf)
        log.info(f'Server started on port: {conf.nw_port}')
    except OSError as e:
        print(f'Error creating socket {e}')
        return
    # Start server control thread
    Thread(
        target=server_handler,
        args=(stop_thread, client_storage, data_storage, log, ca),
        daemon=True).start()
    try:
        run_accept_loop(
            sock, stop_thread, conf, ca, log,
            client_storage, data_storage, client_threads
        )
    except KeyboardInterrupt:
        log.warning('Server stopped by Ctrl-C')
    finally:
        shutdown_server(stop_thread, client_storage, client_threads, sock, log)


if __name__ == '__main__':
    main()