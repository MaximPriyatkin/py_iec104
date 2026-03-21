"""
Common utilities for IEC 104 driver.

This module provides core functionality for the IEC 104 driver including:
- Configuration loading from TOML files
- Logging setup with rotation
- Signal data structures and storage
- Client state management
- CSV signal configuration loading
"""

import logging
from logging.handlers import RotatingFileHandler
import sys
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import datetime
import time
import tomllib
import queue
from typing import Optional, Any, Callable, Iterable
import socket
from threading import Lock, Event, RLock
import const
import csv


# ---- Driver Configuration Loading ----
@dataclass
class Conf:
    """Configuration dataclass for the IEC 104 driver.

    Holds all configuration parameters loaded from TOML file.
    """

    nw_port: int
    nw_max_client: int
    nw_bind_ip: str
    nw_allow_ip: list[str]
    prot_ca: int
    prot_t3: int
    prot_k: int
    prot_w: int
    prot_strict_coa: bool
    max_rx_buf: int
    sim_sc: str
    sg_addr: str
    log_file_lvl: str
    log_console_lvl: str
    log_name: str
    log_fname: str
    log_backup: int
    log_size: int
    log_i_frame_stats_every: int  # I-frame statistics logging interval (every N sent)


def load_config(path: str = "config.toml") -> Conf:
    """Load configuration from TOML file.

    Args:
        path: Path to the TOML configuration file.

    Returns:
        Conf object populated with configuration values.

    Raises:
        FileNotFoundError: If configuration file doesn't exist.
        tomllib.TOMLDecodeError: If TOML parsing fails.

    Example:
        >>> conf = load_config("my_config.toml")
        >>> print(conf.nw_port)
        2404
    """
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    return Conf(
        nw_port=data['nw']['port'],
        nw_max_client=data['nw']['max_clients'],
        nw_bind_ip=data['nw']['bind_ip'],
        nw_allow_ip=data['nw']['allow_ip'],
        prot_ca=data['prot']['ca'],
        prot_t3=data['prot']['t3'],
        prot_k=data['prot']['k'],
        prot_w=data['prot']['w'],
        prot_strict_coa=data['prot'].get('strict_coa', True),
        max_rx_buf=data['prot']['max_rx_buf'],
        sim_sc=data['sim']['sc'],
        sg_addr=data['sg']['addr'],
        log_file_lvl=data['log']['file_lvl'],
        log_console_lvl=data['log']['console_lvl'],
        log_fname=data['log']['fname'],
        log_name=data['log']['name'],
        log_backup=data['log']['backup'],
        log_size=data['log']['size'],
        log_i_frame_stats_every=data['log'].get('i_frame_stats_every', 1000),
    )


# ---- Logging Setup ----
def setup_logging(conf: Conf) -> logging.Logger:
    """Configure logger with format "timestamp\tname\tlevel\tmessage".

    Configures both file (with rotation) and console logging handlers.
    Uses RotatingFileHandler to manage log file size and backup count.

    Args:
        conf: Configuration object with logging parameters:
            - log_name: Logger name (appears in each record)
            - log_file_lvl: File logging level (DEBUG, INFO, etc.)
            - log_console_lvl: Console logging level
            - log_fname: Path to log file
            - log_size: Maximum log file size in MB
            - log_backup: Number of backup files to keep

    Returns:
        Configured logger instance.

    Note:
        Time format: YYYY-MM-DD HH:MM:SS.mmm
        Example: 2024-01-15 14:30:25.123
    """
    logger = logging.getLogger(conf.log_name)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s.%(msecs)03d\t%(name)s\t%(levelname)s\t%(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler with rotation
    file_lvl = getattr(logging, conf.log_file_lvl.upper(), logging.DEBUG)
    rotate_handler = RotatingFileHandler(
        conf.log_fname,
        maxBytes=conf.log_size * 1024 * 1024,
        backupCount=conf.log_backup,
        encoding='utf-8'
    )
    rotate_handler.setFormatter(formatter)
    rotate_handler.setLevel(file_lvl)
    logger.addHandler(rotate_handler)

    # Console handler
    console_lvl = getattr(logging, conf.log_console_lvl.upper(), logging.CRITICAL)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(console_lvl)
    logger.addHandler(console_handler)

    return logger


# ---- Signal Configuration ----
@dataclass
class SignalConf:
    """Signal configuration dataclass.

    Stores all information about a single signal point including its
    IEC 104 addressing, value, quality, and change threshold.

    Attributes:
        id: Internal signal identifier.
        ioa: Information Object Address (IEC 104 address).
        asdu: ASDU type identifier.
        name: Human-readable signal name.
        dsc: Optional description.
        val: Current signal value.
        ts: Timestamp of last value update.
        q: Quality byte (0=good, 1=invalid, etc.).
        threshold: Minimum change to trigger update (None = always trigger).
    """
    id: int
    ioa: int
    asdu: int
    name: str
    dsc: str = ''
    val: Any = 0.0
    ts: datetime = field(default_factory=datetime.now)
    q: int = 0x00
    threshold: Optional[float] = None


@dataclass
class IecEvent:
    """IEC 104 event dataclass for queuing and transmission.

    Represents a data point change to be sent to connected clients.

    Attributes:
        id: Internal signal identifier.
        ioa: Information Object Address.
        asdu: ASDU type identifier.
        val: Current signal value.
        ts: Timestamp of the event.
        q: Quality byte.
        cot: Cause of transmission (default 3 = spontaneous).
    """
    id: int
    ioa: int
    asdu: int
    val: Any
    ts: datetime = field(default_factory=datetime.now)
    q: int = 0
    cot: int = 3


def create_data_storage():
    """Create a thread-safe data storage for signals.

    Implements a centralized storage for all signal points with:
    - Indexes by ID, IOA, and name for fast lookup
    - Threshold-based change detection
    - Subscriber notification on value changes
    - Thread-safe operations using locks

    Returns:
        SimpleNamespace with methods:
            add_signal(id, ioa, asdu, name, val, threshold): Add a new signal.
            update_val(val, id=None, ioa=None, q=0, ts=None): Update signal value.
            get_all(): Get all signals as dict.
            get_signal(id=None, ioa=None): Get specific signal.
            get_signal_by_name(pattern): Get signals matching name pattern.
            subscribe(client_id, queue): Subscribe to updates.
            unsubscribe(client_id): Remove subscription.
            get_all_for_gi(): Generator for general interrogation responses.

    Example:
        >>> storage = create_data_storage()
        >>> storage.add_signal(1, 45, 36, "pressure", 0.0, 0.1)
        >>> storage.update_val(100.5, ioa=45)
    """
    _signals = {}
    _ioa_idx = {}
    _name_idx = {}
    _lock = Lock()
    _subs = {}

    def get_all_for_gi():
        """Generate events for all signals for general interrogation.

        Yields:
            IecEvent for each signal with COT=20 (interrogation response).
        """
        with _lock:
            snapshot = list(_signals.values())
        for sg in snapshot:
            yield IecEvent(
                id=sg.id,
                ioa=sg.ioa,
                asdu=sg.asdu,
                val=sg.val,
                ts=sg.ts,
                q=sg.q,
                cot=20
            )

    def add_signal(id, ioa, asdu, name, val, threshold):
        """Add a new signal to storage.

        Args:
            id: Internal signal identifier.
            ioa: Information Object Address.
            asdu: ASDU type identifier.
            name: Human-readable signal name.
            val: Initial value.
            threshold: Minimum change to trigger update.

        Raises:
            ValueError: If IOA already exists.
        """
        with _lock:
            _signals[id] = SignalConf(
                id=id,
                ioa=ioa,
                asdu=asdu,
                name=name,
                val=val,
                ts=datetime.now(),
                threshold=threshold
            )
            if ioa in _ioa_idx:
                raise ValueError(f'IOA {ioa} already exists')
            _ioa_idx[ioa] = id
            _name_idx[name.lower()] = id

    def update_val(val, *, id=None, ioa=None, q=0, ts=None):
        """Update signal value and notify subscribers if threshold exceeded.

        Args:
            val: New value.
            id: Internal signal ID (mutually exclusive with ioa).
            ioa: IOA (mutually exclusive with id).
            q: Quality byte.
            ts: Optional timestamp (defaults to now).

        Returns:
            bool: True if value was updated, False if unchanged or invalid.

        Raises:
            ValueError: If both id and ioa are specified or neither is.
        """
        if (id is not None) == (ioa is not None):
            raise ValueError('Cannot specify both id and ioa simultaneously')
        if ioa is not None:
            id = _ioa_idx.get(ioa)
        if id is None:
            return False
        with _lock:
            sg = _signals.get(id)
            if not sg:
                return False
            q_change = (q != sg.q)
            val_change = False
            if sg.threshold is not None:
                try:
                    if abs(val - sg.val) >= sg.threshold:
                        val_change = True
                except (TypeError, ValueError):
                    val_change = (val != sg.val)
            else:
                val_change = (val != sg.val)
            if not val_change and not q_change:
                return False
            sg.ts = ts or datetime.now()
            sg.val = val
            sg.q = q
            event = IecEvent(id=id, ioa=sg.ioa, asdu=sg.asdu, val=sg.val, ts=sg.ts, q=sg.q)
            if sg.asdu >= 45:
                return True
            targets = list(_subs.values())
        for q in targets:
            q.put_nowait(event)
        return True

    def get_signal(id: int | None = None, ioa: int | None = None):
        """Get a signal by ID or IOA.

        Args:
            id: Internal signal ID (mutually exclusive with ioa).
            ioa: IOA (mutually exclusive with id).

        Returns:
            dict: Dictionary with single signal entry.

        Raises:
            ValueError: If both id and ioa are specified or neither is.
        """
        if (id is not None) == (ioa is not None):
            raise ValueError('Cannot specify both id and ioa simultaneously')
        if id is None:
            id = _ioa_idx[ioa]
        res = {}
        res[id] = _signals[id]
        return dict(res)

    def get_signal_by_name(name_patt: str):
        """Get signals matching name pattern (exact match with regex).

        Args:
            name_patt: Name pattern to match (interpreted as regex).

        Returns:
            dict: Dictionary of matching signals by ID.
        """
        res = {}
        pattern = re.compile(f'^{name_patt}$')
        for name, id in _name_idx.items():
            if pattern.search(name):
                res[id] = _signals[id]
        return dict(res)

    def get_all():
        """Get all signals.

        Returns:
            dict: Copy of all signals by ID.
        """
        with _lock:
            return dict(_signals)

    def subscribe(client_id, queue):
        """Subscribe a client to value updates.

        Args:
            client_id: Unique client identifier (e.g., address tuple).
            queue: Queue to receive IecEvent updates.
        """
        with _lock:
            _subs[client_id] = queue

    def unsubscribe(client_id):
        """Unsubscribe a client from value updates.

        Args:
            client_id: Client identifier to remove.
        """
        with _lock:
            if client_id in _subs:
                del _subs[client_id]

    return SimpleNamespace(
        add_signal=add_signal,
        update_val=update_val,
        get_all=get_all,
        get_signal_by_name=get_signal_by_name,
        subscribe=subscribe,
        unsubscribe=unsubscribe,
        get_all_for_gi=get_all_for_gi,
        get_signal=get_signal
    )


def load_signal(add_signal: Callable, ca: int, fname: str = 'signals.csv') -> None:
    """Load signal configuration from CSV file.

    Reads signal definitions from a tab-separated CSV file and adds them
    to the signal storage. Filters signals by Common Address (ca).

    Args:
        add_signal: Function to add a signal (from create_data_storage).
        ca: Common Address to filter signals.
        fname: Path to CSV configuration file (default: 'signals.csv').

    Expected CSV columns:
        ca, id, ioa, asdu, name, val, threshold

    Example:
        >>> load_signal(storage.add_signal, 1, "signals.csv")
    """
    with open(fname, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            if ca != int(row['ca']):
                continue
            asdu = int(row['asdu'])
            val = get_val_by_asdu(asdu, row['val'])
            if len(row['threshold']) > 0:
                threshold = float(row['threshold'])
            else:
                threshold = None
            add_signal(
                int(row['id']),
                int(row['ioa']),
                asdu,
                row['name'],
                val,
                threshold
            )


def get_val_by_asdu(type_asdu: int, val: str) -> int | float | str :
    """Convert string value to appropriate type based on ASDU.

    Args:
        type_asdu: ASDU type identifier.
        val: String representation of the value.

    Returns:
        int for integer ASDUs, float for floating-point ASDUs.

    Example:
        >>> get_val_by_asdu(36, "100.5")  # Float ASDU
        100.5
        >>> get_val_by_asdu(30, "1")      # Integer ASDU
        1
    """
    val = val.strip().replace(',', '.')
    if type_asdu in const.INT_ASDU:
        return int(val)
    elif type_asdu in const.FLOAT_ASDU:
        return float(val)
    else:
        return val



def print_signals(sg_dict: dict) -> None:
    """Print signals in a formatted table.

    Args:
        sg_dict: Dictionary of signals (ID -> SignalConf) to print.

    Example:
        >>> storage = create_data_storage()
        >>> storage.add_signal(1, 45, 36, "pressure", 0.0, 0.1)
        >>> print_signals(storage.get_all())
        ------------------------------------------------------------------------
        ID       | IOA      | TYPE   | Name                                | Value    | Threshold
        ------------------------------------------------------------------------
        1        | 45       | 36     | pressure                            | 0.0      | 0.1
        ------------------------------------------------------------------------
    """
    if len(sg_dict) == 0:
        return
    header = f"{'ID':<8} | {'IOA':<8} | {'TYPE':<6} | {'Name':<35} | {'Value':<8} | {'Threshold'}"
    separator = "-" * len(header)
    print('\n' + separator)
    print(header)
    print(separator)
    sorted_sg = sorted(sg_dict.keys())
    for row in sorted_sg:
        sg = sg_dict[row]
        print(f'{row:<8} | {sg.ioa:<8} | {sg.asdu:<6} | {sg.name:<35} | {sg.val:<8} | {sg.threshold}')
    print(separator)


# ---- Client State Management ----
@dataclass
class ClientState:
    """State object for a connected client.

    Maintains all protocol state, synchronization primitives, and
    communication resources for a single client connection.

    Attributes:
        ca: Common Address for this client.
        rec_sq: Receive sequence number (N(S) expected).
        send_sq: Send sequence number (V(S)).
        startdt_confirmed: Whether STARTDT has been confirmed.
        stop_event: Event to signal thread termination.
        addr: Client address tuple (ip, port).
        conn: Connected socket.
        log: Logger for this client.
        conf: Configuration reference.
        last_rec: Last receive time (monotonic).
        last_send: Last send time (monotonic).
        seq_lock: RLock for sequence number protection.
        sock_lock: Lock for socket write serialization.
        out_que: Outbound message queue.
        on_command: Callback for command processing.
        on_gi: Callback for general interrogation.
        rec_count_since_send: Count of received I-frames without S-frame.
        last_ack_nr: Last acknowledged N(R) from client.
        sent_obj: Count of sent ASDU objects (for statistics).
        rx_buf: Receive buffer for frame assembly.
    """
    ca: int = 0
    rec_sq: int = 0
    send_sq: int = 0
    startdt_confirmed: bool = False
    stop_event: Event = field(default_factory=Event)
    addr: Optional[tuple] = None
    conn: Optional[socket.socket] = None
    log: Optional[logging.Logger] = None
    conf: Optional['Conf'] = None
    # Time for interval calculations - use monotonic to avoid system clock drift
    last_rec: float = field(default_factory=time.monotonic)
    last_send: float = field(default_factory=time.monotonic)
    # Protects protocol sequences (send_sq/rec_sq/ack) and socket writes
    seq_lock: RLock = field(default_factory=RLock)
    # Serializes TCP socket writes between read/write threads
    sock_lock: Lock = field(default_factory=Lock)
    out_que: Optional[queue.Queue] = None
    on_command: Optional[Callable[[Any, int], None]] = None
    on_gi: Optional[Callable[[], Iterable[IecEvent]]] = None
    rec_count_since_send: int = 0  # Received I-frames without S-frame (for w-window)
    last_ack_nr: int = 0  # Last N(R) from client - acknowledged our I-frames (for k-window)
    sent_obj: int = 0  # Counter for sent ASDU objects (for statistics)
    rx_buf: bytearray = field(default_factory=bytearray)


def create_client_storage():
    """Create thread-safe storage for client states.

    Implements a centralized storage for all connected clients with
    thread-safe operations for adding, removing, and listing clients.

    Returns:
        SimpleNamespace with methods:
            get_clients(): Get copy of all clients.
            add_client(state): Add a client.
            remove_client(addr): Remove a client by address.
            close_all(): Close all client connections.

    Example:
        >>> storage = create_client_storage()
        >>> storage.add_client(client_state)
        >>> clients = storage.get_clients()
        >>> storage.close_all()
    """
    _clients = {}
    _lock = Lock()

    def get_clients():
        with _lock:
            return _clients.copy()

    def add_client(state):
        with _lock:
            _clients[state.addr] = state

    def remove_client(addr):
        with _lock:
            _clients.pop(addr, None)

    def close_all():
        with _lock:
            for addr, state in list(_clients.items()):
                try:
                    state.conn.close()
                    state.stop_event.set()
                except Exception:
                    if state.log:
                        state.log.error(f'Error stopping {addr}')
                del _clients[addr]

    return SimpleNamespace(
        get_clients=get_clients,
        add_client=add_client,
        remove_client=remove_client,
        close_all=close_all
    )