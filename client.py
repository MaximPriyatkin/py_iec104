import socket
import struct
import queue
import time
from datetime import datetime
from threading import Thread, Event, Lock
from types import SimpleNamespace

import common as cm
import const
import protocol as prt
from control_client import client_handler

MAX_CONNECTIONS = 8


def create_history_writer(filename: str):
    """Create a thread-safe TSV writer for signal history."""
    _lock = Lock()
    _file = open(filename, 'a', encoding='utf-8')
    if _file.tell() == 0:
        _file.write("timestamp\tsession\tioa\tasdu\tvalue\tquality\tcot\n")
        _file.flush()

    def write(session_name: str, objects: list):
        with _lock:
            for ioa, asdu, val, q, cot, _coa, ts in objects:
                ts_str = ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:23] if ts else datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:23]
                _file.write(f"{ts_str}\t{session_name}\t{ioa}\t{asdu}\t{val}\t{q}\t{cot}\n")
            _file.flush()

    def close():
        _file.close()

    return SimpleNamespace(write=write, close=close)

# state в client_send/client_rec всегда инициализирован 
# pyright: reportOptionalMemberAccess=false

def build_cmd_packet(state: cm.ClientState, asdu: int, ioa: int, val: float | int, cot: int) -> bytes | None:
    """Build an I-frame packet for a command.

    Constructs a command packet for the IEC 104 protocol based on the provided
    parameters. The packet is built using the current send sequence number
    from the client state.

    Args:
        state: Client state object containing connection and sequence info.
        asdu: ASDU type identifier (e.g., C_IC_NA_1 for general interrogation).
        ioa: Information Object Address (target signal address).
        val: Value to send (command parameter).
        cot: Cause of transmission (e.g., activation, deactivation).

    Returns:
        The constructed packet as bytes, or None if packet building failed.

    Example:
        >>> packet = build_cmd_packet(state, const.AsduTypeId.C_IC_NA_1, 0, 20, const.COT.ACTIVATION)
    """
    event = cm.IecEvent(id=-1, ioa=ioa, asdu=asdu, val=val, cot=cot)
    return prt.build_i_frame(state, [event])

def recv_loop(state: cm.ClientState):
    """Receive loop for processing incoming data from the server.

    This function runs in a dedicated thread, continuously receiving data from
    the socket, buffering it, and processing complete frames. It handles
    socket timeouts and connection errors gracefully.

    The loop implements the IEC 104 protocol receive logic, including:
    - Frame buffering and reconstruction
    - Frame type detection (I, S, U)
    - Sequence number validation
    - S-frame sending for window size management

    Args:
        state: Client state object containing socket, buffer, and protocol state.

    Note:
        The function exits when stop_event is set or the connection is closed.
    """
    if state is None:
        return
    log = state.log
    conn = state.conn
    conn.settimeout(1.0)
    while not state.stop_event.is_set():
        try:
            data = conn.recv(1024)
            if not data:
                state.stop_event.set()
                break
            state.rx_buf.extend(data)
            while len(state.rx_buf) >= 6:
                if state.rx_buf[0] != 0x68:
                    try:
                        idx = state.rx_buf.index(0x68)
                        del state.rx_buf[:idx]
                    except ValueError:
                        state.rx_buf.clear()
                        break
                if len(state.rx_buf) < 2:
                    break
                total_len = state.rx_buf[1] + 2
                if len(state.rx_buf) < total_len:
                    break
                frame = bytes(state.rx_buf[:total_len])
                del state.rx_buf[:total_len]
                process_frame(state, frame)
        except socket.timeout:
            continue
        except (ConnectionError, OSError):
            state.stop_event.set()
            break
    log.info("Receive loop stopped")

def send_loop(state: cm.ClientState):
    """Send loop for transmitting data to the server.

    This function runs in a dedicated thread, continuously fetching packets
    from the outbound queue and sending them over the socket. It also
    handles keepalive (TESTFR) messages when the channel is idle.

    The loop implements:
    - Queued packet transmission with sequence number management
    - T3 timer for idle channel keepalive
    - Thread-safe socket access with locks
    - Graceful shutdown on connection errors

    Args:
        state: Client state object containing queue, socket, and timing info.

    Note:
        The function exits when stop_event is set or the connection fails.
    """
    log = state.log
    get_time = time.monotonic
    while not state.stop_event.is_set():
        try:
            packet = state.out_que.get(timeout=1.0)
            with state.seq_lock:
                with state.sock_lock:
                    state.conn.send(packet)
                state.last_send = get_time()
                if packet[2] & 0x01 == 0:
                    state.send_sq = (state.send_sq + 1) % 32768
        except queue.Empty:
            now = get_time()
            if (now - state.last_send) >= state.conf.prot_t3:
                with state.seq_lock:
                    with state.sock_lock:
                        state.conn.send(const.TESTFR_ACT)
                    state.last_send = now
                log.debug(f"S->KP [TESTFR ACT] Channel idle for {state.conf.prot_t3}s")
            continue
        except (ConnectionError, OSError):
            state.stop_event.set()
            break
    log.info("The sending flow is stopped")

def process_frame(state: cm.ClientState, frame: bytes):
    """Process an incoming IEC 104 frame.

    This function parses and handles different types of frames:
    - I-frames: Information transfer with sequence numbers
    - S-frames: Supervisory (acknowledgement) frames
    - U-frames: Unnumbered (control) frames

    For I-frames, it validates sequence numbers and may send S-frames
    when the receive window is full. For U-frames, it handles STARTDT_CON
    and responds to TESTFR_ACT with TESTFR_CON.

    Args:
        state: Client state object for updating protocol state.
        frame: Raw frame bytes to process.

    Note:
        Frame format is defined in IEC 60870-5-104 standard.
    """
    log = state.log
    if not (frame[2] & 0x01):  # I-frame
        n_s = struct.unpack("<H", frame[2:4])[0] >> 1
        n_r = struct.unpack("<H", frame[4:6])[0] >> 1
        with state.seq_lock:
            state.last_ack_nr = n_r
            if n_s != state.rec_sq:
                log.warning(f"S->C [SEQ ERROR] expected N(S)={state.rec_sq}, but came {n_s}")
            state.rec_sq = (state.rec_sq + 1) % 32768
            state.rec_count_since_send += 1
            if state.rec_count_since_send >= state.conf.prot_w:
                s_packet = prt.build_s_frame(state)
                with state.sock_lock:
                    state.conn.send(s_packet)
                state.rec_count_since_send = 0
                state.last_send = time.monotonic()
        asdu_type = frame[6] if len(frame) > 6 else -1
        log.debug(f"S->C [I-FRAME] ASDU:{asdu_type} N(S):{n_s} N(R):{n_r}")
        if state.on_data:
            objects = prt.decode_i_frame_objects(frame)
            if objects:
                state.on_data(state.session_name, objects)
        return
    if frame[2] & 0x02:  # U-frame
        try:
            u_cmd = const.UTypeId(frame[2])
        except ValueError:
            log.warning(f"S->C [U-FRAME] Unknown type {frame.hex(' ').upper()}")
            return
        log.debug(f"S->C [U-FRAME] {u_cmd.name}")
        if u_cmd == const.UTypeId.STARTDT_CON:
            state.startdt_confirmed = True
        if u_cmd == const.UTypeId.TESTFR_ACT:
            with state.sock_lock:
                state.conn.send(const.U_RESP[const.UTypeId.TESTFR_ACT])
            state.last_send = time.monotonic()
        return
    # S-frame
    n_r = struct.unpack("<H", frame[4:6])[0] >> 1
    with state.seq_lock:
        state.last_ack_nr = n_r
    log.debug(f"S->C [S-FRAME] N(R):{n_r}")

def create_client_socket(ip: str, port: int):
    """Create and connect a TCP socket to the remote server.

    Sets up a TCP socket with Nagle's algorithm disabled (TCP_NODELAY)
    for low latency communication, which is critical for real-time
    industrial control applications.

    Args:
        ip: IP address of the remote server (RTU/PLC).
        port: Port number (typically 2404 for IEC 104).

    Returns:
        Connected socket object.

    Raises:
        socket.error: If connection fails.

    Example:
        >>> sock = create_client_socket("192.168.1.10", 2404)
    """
    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn.connect((ip, port))
    return conn

def create_session_state(name: str, conn: socket.socket, ip: str, port: int, ca: int, conf: cm.Conf, on_data=None):
    """Create and initialize a client state object."""
    state = cm.ClientState()
    state.session_name = name
    state.addr = (ip, port)
    state.ca = ca
    state.conn = conn
    state.conf = conf
    state.log = cm.logging.getLogger(f"{conf.log_name}.client.{name}")
    state.out_que = queue.Queue()
    state.rx_buf = bytearray()
    state.on_data = on_data
    return state

def start_session_threads(state: cm.ClientState):
    """Start send and receive threads for a client session.

    Creates and starts two daemon threads:
    - send_loop: Handles outgoing data transmission
    - recv_loop: Handles incoming data reception

    Args:
        state: Client state object for the session.

    Returns:
        Tuple of (send_thread, receive_thread).

    Example:
        >>> send_t, recv_t = start_session_threads(state)
    """
    send_t = Thread(target=send_loop, args=(state,), daemon=True)
    recv_t = Thread(target=recv_loop, args=(state,), daemon=True)
    send_t.start()
    recv_t.start()
    return send_t, recv_t

def start_session(name: str, ip: str, port: int, ca: int, conf: cm.Conf, root_log, on_data=None):
    """Start a complete client session."""
    conn = create_client_socket(ip, port)
    state = create_session_state(name, conn, ip, port, ca, conf, on_data)
    send_t, recv_t = start_session_threads(state)
    root_log.info(f"Session {name} connected to {ip}:{port} ca={ca}")
    return state, send_t, recv_t

def create_client_pool():
    """Create a session pool manager for managing multiple client connections.

    The pool provides thread-safe operations for managing multiple
    concurrent client sessions. It handles session lifecycle management,
    including creation, lookup, removal, and cleanup.

    Returns:
        SimpleNamespace with methods:
            list_sessions(): Returns dict of all sessions.
            add_session(name, state, threads): Add a session.
            get_state(name): Get session state by name.
            remove_session(name): Remove and cleanup a session.
            close_all(): Close all sessions.

    Example:
        >>> pool = create_client_pool()
        >>> pool.add_session("plc1", state, (send_t, recv_t))
        >>> state = pool.get_state("plc1")
    """
    _lock = Lock()
    _sessions = {}

    def list_sessions():
        with _lock:
            return dict(_sessions)

    def add_session(name, state, threads):
        with _lock:
            if name in _sessions:
                raise ValueError(f"The session {name} already exists")
            if len(_sessions) >= MAX_CONNECTIONS:
                raise ValueError(f"Connection limit reached: {MAX_CONNECTIONS}")
            _sessions[name] = (state, threads)

    def get_state(name):
        with _lock:
            item = _sessions.get(name)
            if item is None:
                return None
            return item[0]

    def remove_session(name):
        with _lock:
            item = _sessions.pop(name, None)
        if item is None:
            return False
        state, (send_t, recv_t) = item
        state.stop_event.set()
        try:
            state.conn.close()
        except Exception:
            pass
        send_t.join(timeout=2.0)
        recv_t.join(timeout=2.0)
        return True

    def close_all():
        for name in list(list_sessions().keys()):
            remove_session(name)

    return SimpleNamespace(
        list_sessions=list_sessions,
        add_session=add_session,
        get_state=get_state,
        remove_session=remove_session,
        close_all=close_all,
    )

def create_client_api(pool, conf: cm.Conf, log, on_data=None):
    """Create a high-level API for controlling client connections."""
    api = SimpleNamespace()

    def connect(name: str, ip: str, port: int, ca: int):
        if len(pool.list_sessions()) >= MAX_CONNECTIONS:
            raise ValueError(f"Connection limit reached: {MAX_CONNECTIONS}")
        state, send_t, recv_t = start_session(name, ip, int(port), int(ca), conf, log, on_data)
        pool.add_session(name, state, (send_t, recv_t))

    def disconnect(name: str):
        if not pool.remove_session(name):
            raise ValueError(f"Session {name} not found")

    def startdt(name: str):
        state = pool.get_state(name)
        if not state:
            raise ValueError(f"Session {name} not found")
        state.out_que.put(const.STARTDT_ACT)

    def gi(name: str):
        state = pool.get_state(name)
        if not state:
            raise ValueError(f"Session {name} not found")
        packet = build_cmd_packet(state, const.AsduTypeId.C_IC_NA_1, 0, 20, const.COT.ACTIVATION)
        if packet is None:
            raise ValueError("couldn't build a GI package")
        state.out_que.put(packet)

    api.connect = connect
    api.disconnect = disconnect
    api.startdt = startdt
    api.gi = gi
    api.list_sessions = pool.list_sessions
    api.load_config = cm.load_connections
    return api

def run_client_loop(stop_thread: Event, pool=None, log=None):
    """Main client loop with dead session detection."""
    try:
        while not stop_thread.is_set():
            time.sleep(1.0)
            if pool:
                for name, (state, _) in list(pool.list_sessions().items()):
                    if state.stop_event.is_set():
                        pool.remove_session(name)
                        if log:
                            log.warning(f"Session {name} disconnected, removed")
    except KeyboardInterrupt:
        stop_thread.set()

def shutdown_client(stop_thread: Event, pool, log):
    """Gracefully shut down the client and clean up resources.

    Sets the stop event, closes all sessions, and logs the shutdown.

    Args:
        stop_thread: Event to signal threads to stop.
        pool: Session pool to close all sessions.
        log: Logger for shutdown message.
    """    
    stop_thread.set()
    pool.close_all()
    log.info("Client stopped")

def main():
    """Main entry point for the client application."""
    conf = cm.load_config()
    log = cm.setup_logging(conf)
    stop_thread = Event()
    pool = create_client_pool()
    history = create_history_writer(conf.history_file) if conf.history_file else None
    api = create_client_api(pool, conf, log, history.write if history else None)
    # Auto-connect from config
    for c in cm.load_connections():
        try:
            api.connect(c.name, c.ip, c.port, c.ca)
            log.info(f"Auto-connected: {c.name} -> {c.ip}:{c.port} ca={c.ca}")
            if c.auto_start:
                api.startdt(c.name)
            if c.auto_gi:
                api.gi(c.name)
        except Exception as e:
            log.error(f"Auto-connect {c.name} failed: {e}")
    Thread(target=client_handler, args=(stop_thread, api, log, conf.log_name), daemon=True).start()
    try:
        run_client_loop(stop_thread, pool, log)
    finally:
        shutdown_client(stop_thread, pool, log)
        if history:
            history.close()

if __name__ == "__main__":
    main()
