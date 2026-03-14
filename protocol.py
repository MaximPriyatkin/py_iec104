import struct
from datetime import datetime
import const
import common as cm

def build_i_frame(state: cm.ClientState, event) -> bytes|None:
    assert state.log is not None
    log = state.log
    id = event.id
    ioa = event.ioa 
    t_asdu = event.asdu 
    val = event.val 
    ts = event.ts 
    q = event.q 
    vsq = 0x01
    cot_bytes = struct.pack('<H', 3)
    asdu_header = struct.pack('<BBBBH', t_asdu, vsq, cot_bytes[0], cot_bytes[1], state.ca)
    ioa_bytes = int.to_bytes(ioa, 3, 'little')
    if t_asdu in (1, 30):
        sp_val = int(val) & 0x01
        siq = bytes([q | sp_val])
        body = ioa_bytes + siq
        if t_asdu == 30:
            body += datetime_to_cp56(ts)
    elif t_asdu in (13, 36):
        body = ioa_bytes + struct.pack('<fB', float(val), q)
        if t_asdu == 36:
            body += datetime_to_cp56(ts)
    else:
        state.log.error(f'Тип ASDU  {t_asdu} не поддерживается драйвером')
        return None
    asdu = asdu_header + body
    send_sq = (state.send_sq << 1).to_bytes(2, 'little')
    rec_sq = (state.rec_sq << 1).to_bytes(2, 'little')
    apdu_header = b'\x68' + bytes([len(asdu) + 4]) + send_sq + rec_sq
    return apdu_header + asdu


def build_i_frame_ack(state:cm.ClientState, frame, cot):
    asdu = bytearray(frame[6:])
    asdu[2] = cot
    ctrl_ns = (state.send_sq << 1).to_bytes(2, 'little')
    ctrl_nr = (state.rec_sq << 1).to_bytes(2, 'little')
    header = b'\x68' + bytes([len(asdu) + 4]) + ctrl_ns + ctrl_nr
    state.send_sq = (state.send_sq + 1) % 32768
    return header + asdu


def proc_frame(frame: bytes, state: cm.ClientState):
    if not (frame[2] & 0x01):
        return 'I', handle_i_frame(frame, state)
    if (frame[2] & 0x02):
        return 'U', handle_u_frame(frame, state)
    return 'S', handle_s_frame(frame, state)

def handle_i_frame(frame: bytes, state: cm.ClientState):
    assert state.log is not None
    log = state.log        
    n_s = struct.unpack('<H', frame[2:4])[0] >> 1
    n_r = struct.unpack('<H', frame[4:6])[0] >> 1
    if n_s != state.rec_sq:
        log.error(f'C->S [SEQ ERROR] Ожидание N(S)={state.rec_sq} пришло {n_s}')
        # выполнить разрыв связи
    state.rec_sq = (state.rec_sq + 1) % 32768
    # проверить наши кадры на доставку
    asdu = frame[6:]
    type_id = asdu[0]
    vsq = asdu[1]
    cot = asdu[2]

    coa =  struct.unpack('<H', asdu[3:5])[0]
    # проверить coa!

    count = vsq & 0x7F # Количество объектов 0-6 бит
    is_seq = vsq & 0x80 # 7ой бит - тип последовательности
    offset = 6 # начало данных в asdu
    parsed_obj = []
    try:
        if is_seq:
            # один базовый ioa, затем значения
            base_ioa = struct.unpack('<I', asdu[offset:offset+3] + b'\x00')[0]
            offset += 3
            val_size = const.ASDU_DATA_SIZE.get(type_id, 1)
            for i in range(count):
                val_data = asdu[offset:offset+val_size]
                parsed_obj.append((base_ioa + i, val_data))
                offset += val_size
        else:
            # много базовых ioa
            val_size = const.ASDU_DATA_SIZE.get(type_id, 1)
            for _ in range(count):
                ioa = int.from_bytes(asdu[offset:offset+3], byteorder='little')
                offset += 3
                val_data = asdu[offset:offset+val_size]
                parsed_obj.append((ioa, val_data))
                offset += val_size
    except IndexError:
        log.error("C->S [ASDU] Ошибка длины: пакет обрезан")
        return None
             
    if type_id == const.AsduTypeId['C_IC_NA_1']: # общий опрос
        return build_i_frame_ack(state, frame, const.COT.ACTIVATION_CON)

    if type_id == const.AsduTypeId['C_SC_NA_1']: # команда однопозиционная
        for ioa, data in parsed_obj:

            val = data[0] & 0x01
            log.info(f'C->S [COMMAND] IOA:{ioa} VAL:{val}')
        return build_i_frame_ack(state, frame, const.COT.ACTIVATION_CON)


    return None



def handle_u_frame(frame: bytes, state: cm.ClientState):
    assert state.log is not None
    u_type_byte = frame[2]
    log = state.log
    try:
        u_cmd = const.UTypeId(u_type_byte)
        if u_cmd == const.UTypeId.STARTDT_ACT:
            log.info(f'C->S [STARTDT ACT] {frame.hex(" ").upper()}')
            state.startdt_confirmed = True
        elif u_cmd == const.UTypeId.STOPDT_ACT:
            log.info(f'C->S [STOPTDT ACT] {frame.hex(" ").upper()}')
            state.startdt_confirmed = False
        elif u_cmd == const.UTypeId.TESTFR_ACT:
            log.info(f'C->S [TESTFR ACT] {frame.hex(" ").upper()}')
        return const.U_RESP.get(u_cmd)
    except ValueError:
         log.warning(f'C->S [U-FRAME] Ошибочный байт {frame.hex(" ").upper()}')

def handle_s_frame(frame, state: cm.ClientState):
    pass 

def datetime_to_cp56(dt: datetime, iv = False) -> bytes:
    # ms в текущей минуте (little-endian)
    ms_total = (dt.second * 1000 + dt.microsecond // 1000) % 60000  # UTC, 0-59999
    ms_low = ms_total & 0xFF
    ms_high = (ms_total >> 8) & 0xFF
    res = bytearray(7)
    res[0] = ms_low
    res[1] = ms_high
    res[2] = dt.minute & 0x3F
    if iv:
        res[2] |= 0x80
    res[3] = dt.hour & 0x1F
    res[4] = (dt.day & 0x1F) | ((dt.isoweekday() & 0x07) << 5)
    res[5] = dt.month & 0x0F
    res[6] = (dt.year - 2000) & 0x7F  # Явно -2000
    return bytes(res)

def datetime_from_cp56(dt_bt):
    if len(dt_bt) < 7:
        raise ValueError('не достаточно данных в cp56')
    ms_total = (dt_bt[1] << 8) | dt_bt[0]
    sec = ms_total // 1000
    msec = ms_total % 1000
    mins = dt_bt[2] & 0x3F
    iv = (dt_bt[2] & 0x80) != 0

    hour = dt_bt[3] & 0x1F
    day = dt_bt[4] & 0x1F
    month = dt_bt[5] & 0x0F
    year = 2000 + (dt_bt[6] & 0x7F)
    try:
        dt = datetime(year, month, day, hour, mins, sec, microsecond=msec*1000)
        return dt , iv
    except ValueError as e:
        return None, None
    





if __name__ == '__main__':
    b = datetime_to_cp56(datetime.now())
    print(b)
    c = datetime_from_cp56(b)
    print(c)
    

