import logging
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import datetime
import time
import tomllib
import queue
from typing import Optional, Any, Callable, Iterable
import socket
from threading import Lock, Event
import const
import csv



# ---- Загрузка конфигурации драйвера ----
@dataclass
class Conf():
    nw_port: int
    nw_max_client: int
    nw_bind_ip: str
    nw_allow_ip: list[str]
    prot_ca: int
    prot_t3: int
    prot_k: int
    prot_w: int
    max_rx_buf: int
    send_sleep: float  # пауза (с) после отправки I-кадра; > 0 снижает CPU на пакетировании
    sim_sc: str
    sg_addr: str
    log_lvl: str
    log_name: str
    log_fname: str
    log_i_frame_stats_every: int  # интервал вывода в лог статистики I-frame (раз в N отправленных)


def load_config(path="config.toml"):
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
        max_rx_buf=data['prot']['max_rx_buf'],
        send_sleep=float(data['prot'].get('send_sleep', 0)),
        sim_sc=data['sim']['sc'],
        sg_addr = data['sg']['addr'],
        log_lvl=data['log']['lvl'],
        log_fname=data['log']['fname'],
        log_name=data['log']['name'],
        log_i_frame_stats_every=data['log'].get('i_frame_stats_every', 1000),
        )

# ---- Логгирование ----
def setup_logging(conf:Conf):
    """Настройка логгирования для драйверов
    """    

    logger = logging.getLogger(conf.log_name)
    num_lvl = getattr(logging, conf.log_lvl.upper(), logging.DEBUG)
    logger.setLevel(num_lvl)

    formater = logging.Formatter(
        '%(asctime)s.%(msecs)03d\t%(name)s\t%(levelname)s\t%(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler = logging.FileHandler(conf.log_fname, encoding='utf-8')
    #file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formater)

    console_handler = logging.StreamHandler(sys.stdout)
    #console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formater)

    logger.addHandler(file_handler)
    #logger.addHandler(console_handler)

    return logger

# ---- Конфигурация сигналов ----
@dataclass
class SignalConf:
    id: int
    ioa: int		
    asdu: int
    name: str
    dsc: str = ''
    val: Any = 0.0
    ts: datetime =  field(default_factory=datetime.now)
    q: int = 0x00
    threshold: Optional[float] = None

@dataclass
class IecEvent:
    id: int
    ioa: int
    asdu: int
    val: Any
    ts: datetime = field(default_factory=datetime.now)
    q: int = 0
    cot: int = 3


def create_data_storage():
    _signals = {}
    _ioa_idx = {}
    _lock = Lock()
    _subs = {} 

    def get_all_for_gi():
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
        with _lock:
            _signals[id] = SignalConf(
                id = id,
                ioa = ioa,
                asdu = asdu,
                name = name,
                val = val,
                ts = datetime.now(),
                threshold=threshold
            )
            if ioa in _ioa_idx:
                raise ValueError(f'IOA {ioa} уже существует')
            _ioa_idx[ioa] = id
    
    def update_val(new_val, *, id = None, ioa = None, new_q=0x00, ts=None):
        if (id is not None) == (ioa is not None):
            raise ValueError ('Нельзя определять id и ioa одновременно')
        if ioa is not None:
            id = _ioa_idx.get(ioa)
        if id is None:
            return False
        with _lock:
            sg = _signals.get(id)
            if not sg:
                return False
            q_change = (new_q != sg.q)
            val_change = False
            if sg.threshold is not None:
                try:
                    if abs(new_val - sg.val) >= sg.threshold:
                        val_change = True
                except (TypeError, ValueError):
                    val_change = (new_val != sg.val)
            else:
                val_change = (new_val != sg.val)
            if not val_change and not q_change:
                return False
            sg.ts = ts or datetime.now()
            sg.val = new_val
            sg.q = new_q
            event = IecEvent(id=id, ioa=sg.ioa, asdu=sg.asdu, val=sg.val, ts=sg.ts, q=sg.q)
            if sg.asdu >= 45:
                return True
            targets = list(_subs.values())
        for q in targets:
            q.put_nowait(event)
        return True

    def get_all():
        with _lock:
            return dict(_signals)

    def subscribe(client_id, queue):
        with _lock:
            _subs[client_id] = queue

    def unsubscribe(client_id):
        with _lock:
            if client_id in _subs:
                del _subs[client_id]

    return SimpleNamespace(add_signal=add_signal,
                           update_val=update_val,
                           get_all=get_all,
                           subscribe=subscribe,
                           unsubscribe=unsubscribe,
                           get_all_for_gi=get_all_for_gi)

def load_signal(add_signal:Callable, ca:int, fname: str='signals.csv') :
    """Функция загрузки конфигурации сигналов из файла csv

    Args:
        add_signal (Callable): функция добавления сигнала в create_data_storage
        ca (int): номер КП, который нужно выбрать из базы
        fname (str, optional): имя файла конфигурации сигналов. Defaults to 'signals.csv'.
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
            add_signal(int(row['id']),
                       int(row['ioa']),
                       asdu,
                       row['name'],
                       val,
                       threshold
            )

def get_val_by_asdu(type_asdu:int, val:str):
    val = val.strip().replace(',','.')
    if type_asdu in const.INT_ASDU:
        return int(val)
    if type_asdu in const.FLOAT_ASDU:
        return float(val)

def print_signals(sg_dict: dict):
    header = f"{'ID':<8} | {'IOA':<8} | {'TYPE':<6} | {'Name':<15} | {'Value':<8} | {'Threshold'}"
    separator = "-" * len(header)
    print('\n' + separator)
    print(header)
    print(separator)
    sorted_sg = sorted(sg_dict.keys())
    for row in sorted_sg:
        sg = sg_dict[row]
        print(f'{row:<8} | {sg.ioa:<8} | {sg.asdu:<6} | {sg.name:<15} | {sg.val:<8} | {sg.threshold}')
    print(separator)


# ---- Состояние клиентов ----
@dataclass
class ClientState:
    ca: int = 0
    rec_sq: int = 0
    send_sq: int = 0
    startdt_confirmed: bool = False
    stop_event: Event = field(default_factory=Event)
    addr: Optional[tuple] = None
    conn: Optional[socket.socket] = None
    log: Optional[logging.Logger] = None
    conf: Optional['Conf'] = None
    last_rec: float = field(default_factory=time.time)
    last_send: float = field(default_factory=time.time)
    out_que: Optional[queue.Queue] = None
    on_command: Optional[Callable[[Any, int], None]] = None
    on_gi: Optional[Callable[[], Iterable[IecEvent]]] = None
    rec_count_since_send: int = 0  # число принятых I-кадров без ответа (для отправки S по w)
    last_ack_nr: int = 0  # последний N(R) от клиента — подтверждённые им наши I-кадры (для ограничения по k)

def create_client_storage():
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
                        state.log.error(f'Ошибка остановки {addr}')
                del _clients[addr]
    return SimpleNamespace(
        get_clients=get_clients, 
        add_client=add_client, 
        remove_client=remove_client, 
        close_all=close_all)


