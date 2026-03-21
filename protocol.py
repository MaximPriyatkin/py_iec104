"""
Protocol handling for IEC 60870-5-104.

This module implements the core protocol functions for IEC 104 including:
- Encoding of different ASDU types (single point, double point, measured values, etc.)
- I-frame building with sequence number management
- Frame processing (I, S, U frames)
- CP56Time2a time encoding/decoding
- Command handling and response generation
"""

import struct
from datetime import datetime
import const
from const import AsduTypeId
import common as cm

# pyright: reportOptionalMemberAccess=false


_TS_TYPES = frozenset((30, 31, 32, 33, 34, 35, 36, 37))


def _enc_val(type_id: int, ev) -> bytes | None:
    """Encode value and quality into ASDU object data bytes."""
    if type_id in (1, 30, 45):
        return bytes([(int(ev.val) & 0x01) | (ev.q & 0xFE)])
    if type_id in (3, 31, 46):
        return bytes([(int(ev.val) & 0x03) | (ev.q & 0xFC)])
    if type_id in (5, 32):
        return struct.pack('<bB', int(ev.val), ev.q)
    if type_id in (7, 33):
        return struct.pack('<IB', int(ev.val), ev.q)
    if type_id in (9, 11, 34, 35):
        return struct.pack('<hB', int(ev.val), ev.q)
    if type_id in (13, 36):
        return struct.pack('<fB', float(ev.val), ev.q)
    if type_id in (15, 37):
        return struct.pack('<iB', int(ev.val), ev.q)
    if type_id == 100:
        return bytes([int(ev.val) & 0xFF])
    return None


def _enc_obj(t_asdu, ev) -> bytes | None:
    """Encode a single ASDU object (IOA + value + optional timestamp)."""
    data = _enc_val(t_asdu, ev)
    if data is None:
        return None
    body = int.to_bytes(ev.ioa, 3, 'little') + data
    if t_asdu in _TS_TYPES:
        body += datetime_to_cp56(ev.ts)
    return body


def build_i_frame(state: cm.ClientState, events: list) -> bytes | None:
    """Build an I-frame (information frame) from a list of events.

    Constructs a complete IEC 104 I-frame with:
    - APCI header (start byte, length, send/recv sequence numbers)
    - ASDU header (type, count, COT, COA)
    - Encoded objects (IOA + value + optional timestamp)

    Args:
        state: Client state containing sequence numbers and configuration.
        events: List of IecEvent objects (must all have same ASDU and COT).

    Returns:
        Complete I-frame as bytes, or None on error.

    Example:
        >>> events = [IecEvent(id=1, ioa=45, asdu=36, val=100.5)]
        >>> frame = build_i_frame(state, events)
    """
    if not events:
        return None
    t_asdu = events[0].asdu
    cot = events[0].cot
    parts = []
    for ev in events:
        obj = _enc_obj(t_asdu, ev)
        if obj is None:
            state.log.error(f'ASDU type {t_asdu} not supported by driver')
            return None
        parts.append(obj)
    cot_bytes = struct.pack('<H', cot)
    asdu = struct.pack('<BBBBH', t_asdu, len(events), cot_bytes[0], cot_bytes[1], state.ca) + b''.join(parts)
    send_sq = (state.send_sq << 1).to_bytes(2, 'little')
    rec_sq = (state.rec_sq << 1).to_bytes(2, 'little')
    return b'\x68' + bytes([len(asdu) + 4]) + send_sq + rec_sq + asdu


def build_i_frame_ack(state: cm.ClientState, frame, cot) -> bytes:
    """Build an acknowledgment I-frame in response to a command.

    Reuses the ASDU from the incoming frame with modified COT.

    Args:
        state: Client state with sequence numbers.
        frame: Original received frame.
        cot: New cause of transmission (e.g., ACTIVATION_CON).

    Returns:
        Acknowledgment I-frame as bytes.
    """
    asdu = bytearray(frame[6:])
    asdu[2] = cot
    ctrl_ns = (state.send_sq << 1).to_bytes(2, 'little')
    ctrl_nr = (state.rec_sq << 1).to_bytes(2, 'little')
    header = b'\x68' + bytes([len(asdu) + 4]) + ctrl_ns + ctrl_nr
    state.send_sq = (state.send_sq + 1) % 32768
    return header + asdu


def proc_frame(frame: bytes, state: cm.ClientState) -> tuple[str, bytes | None]:
    """Process an incoming frame and determine its type.

    Args:
        frame: Raw frame bytes.
        state: Client state for context.

    Returns:
        Tuple of (frame_type, response):
            - frame_type: 'I', 'S', or 'U'
            - response: Response frame bytes or None

    Example:
        >>> f_type, response = proc_frame(frame, state)
        >>> if response:
        ...     state.conn.send(response)
    """
    if not (frame[2] & 0x01):
        return 'I', handle_i_frame(frame, state)
    if (frame[2] & 0x02):
        return 'U', handle_u_frame(frame, state)
    return 'S', handle_s_frame(frame, state)


def handle_i_frame(frame: bytes, state: cm.ClientState) -> bytes | None:
    """Handle incoming I-frame.

    Processes data frames, extracts ASDUs, handles commands (GI, single/double
    commands, setpoint commands), and builds appropriate responses.

    Args:
        frame: Incoming I-frame bytes.
        state: Client state for context.

    Returns:
        Response frame if required (e.g., for commands), None otherwise.
    """
    log = state.log
    n_s = struct.unpack('<H', frame[2:4])[0] >> 1
    n_r = struct.unpack('<H', frame[4:6])[0] >> 1
    state.last_ack_nr = n_r  # Client acknowledged our I-frames with N(S) < N(R)
    if n_s != state.rec_sq:
        log.error(f'C->S [SEQ ERROR] Expected N(S)={state.rec_sq} received {n_s}')
        # Connection should be terminated
    state.rec_sq = (state.rec_sq + 1) % 32768
    # Check for delivered frames
    asdu = frame[6:]
    if len(asdu) < 6:
        log.error("C->S [ASDU] Length error: ASDU too short")
        return None

    type_id = asdu[0]
    vsq = asdu[1]
    # COT: 2 bytes (little-endian)
    cot = struct.unpack('<H', asdu[2:4])[0]
    # COA: 2 bytes (little-endian) after COT
    coa = struct.unpack('<H', asdu[4:6])[0]

    if state.ca is not None and coa != state.ca:
        strict_coa = True if state.conf is None else getattr(state.conf, 'prot_strict_coa', True)
        if strict_coa:
            log.warning(
                f"C->S [COA] Invalid COA: received {coa}, expected {state.ca}. "
                f"strict_coa=1, ignoring ASDU type {type_id}"
            )
            return None
        # Non-strict mode: accept GI, ignore others
        if type_id == AsduTypeId['C_IC_NA_1']:
            log.warning(
                f"C->S [COA] Invalid COA: received {coa}, expected {state.ca}. "
                f"strict_coa=0, but this is GI, proceeding (ASDU type {type_id})"
            )
        else:
            log.warning(
                f"C->S [COA] Invalid COA: received {coa}, expected {state.ca}. "
                f"strict_coa=0, ignoring ASDU type {type_id}"
            )
            return None

    count = vsq & 0x7F  # Number of objects (bits 0-6)
    is_seq = vsq & 0x80  # Bit 7 - sequence type
    offset = 6  # Start of data in ASDU
    parsed_obj = []
    try:
        if is_seq:
            # One base IOA, then values
            base_ioa = struct.unpack('<I', asdu[offset:offset+3] + b'\x00')[0]
            offset += 3
            val_size = const.ASDU_DATA_SIZE.get(type_id, 1)
            for i in range(count):
                val_data = asdu[offset:offset+val_size]
                parsed_obj.append((base_ioa + i, val_data))
                offset += val_size
        else:
            # Multiple individual IOAs
            val_size = const.ASDU_DATA_SIZE.get(type_id, 1)
            for _ in range(count):
                ioa = int.from_bytes(asdu[offset:offset+3], byteorder='little')
                offset += 3
                val_data = asdu[offset:offset+val_size]
                parsed_obj.append((ioa, val_data))
                offset += val_size
    except IndexError:
        log.error("C->S [ASDU] Length error: packet truncated")
        return None

    # Handle general interrogation (GI)
    if type_id == AsduTypeId['C_IC_NA_1']:
        if state.on_gi and state.out_que:
            for event in state.on_gi():
                if event.asdu < 45:  # Don't send commands in GI
                    state.out_que.put(event)
            state.out_que.put(cm.IecEvent(id=-1, ioa=0, asdu=100, val=0, ts=datetime.now(), cot=10))
            return build_i_frame_ack(state, frame, const.COT.ACTIVATION_CON)

    # Handle single command
    if type_id == AsduTypeId['C_SC_NA_1']:
        for ioa, data in parsed_obj:
            val = data[0] & 0x01
            log.info(f'C->S [COMMAND] IOA:{ioa} VAL:{val}')
            if state.on_command:
                success = state.on_command(val, ioa)
                if not success:
                    log.warning(f'Command on IOA {ioa} rejected')
        return build_i_frame_ack(state, frame, const.COT.ACTIVATION_CON)

    # Handle setpoint command
    if type_id == AsduTypeId['C_SE_NC_1']:
        for ioa, data in parsed_obj:
            val, qos = struct.unpack('<fB', data[:5])
            log.info(f'C->S [COMMAND] IOA:{ioa} VAL:{val}')
            if state.on_command:
                success = state.on_command(val, ioa)
                if not success:
                    log.warning(f'Command on IOA {ioa} rejected')
        return build_i_frame_ack(state, frame, const.COT.ACTIVATION_CON)

    return None


def handle_u_frame(frame: bytes, state: cm.ClientState) -> bytes | None:
    """Handle incoming U-frame (unnumbered control frame).

    Processes control frames: STARTDT, STOPDT, TESTFR.

    Args:
        frame: Incoming U-frame bytes.
        state: Client state for context.

    Returns:
        Response frame (e.g., TESTFR_CON) or None.

    Example:
        >>> response = handle_u_frame(frame, state)
        >>> if response:
        ...     state.conn.send(response)
    """
    u_type_byte = frame[2]
    log = state.log
    try:
        u_cmd = const.UTypeId(u_type_byte)
        if u_cmd == const.UTypeId.STARTDT_ACT:
            log.info(f'C->S [STARTDT ACT] {frame.hex(" ").upper()}')
            state.startdt_confirmed = True
        elif u_cmd == const.UTypeId.STOPDT_ACT:
            log.info(f'C->S [STOPDT ACT] {frame.hex(" ").upper()}')
            state.startdt_confirmed = False
        elif u_cmd == const.UTypeId.TESTFR_ACT:
            log.info(f'C->S [TESTFR ACT] {frame.hex(" ").upper()}')
        return const.U_RESP.get(u_cmd)
    except ValueError:
        log.warning(f'C->S [U-FRAME] Invalid byte {frame.hex(" ").upper()}')


def build_s_frame(state: cm.ClientState) -> bytes:
    """Build an S-frame (supervisory frame) to acknowledge received I-frames.

    Format: 68 04 01 00 (N(R) << 1)

    Args:
        state: Client state containing receive sequence number.

    Returns:
        S-frame as bytes.

    Example:
        >>> s_frame = build_s_frame(state)
        >>> state.conn.send(s_frame)
    """
    nr = (state.rec_sq << 1).to_bytes(2, 'little')
    return b'\x68\x04\x01\x00' + nr


def handle_s_frame(frame: bytes, state: cm.ClientState) -> None:
    """Handle incoming S-frame (supervisory frame).

    Parses N(R) - client acknowledgment of our I-frames with N(S) < N(R).

    Args:
        frame: Incoming S-frame bytes.
        state: Client state to update last_ack_nr.

    Note:
        This is used for flow control (k-window management).
    """
    if len(frame) < 6:
        if state.log:
            state.log.warning('C->S [S-FRAME] Frame too short')
        return
    n_r = struct.unpack('<H', frame[4:6])[0] >> 1
    state.last_ack_nr = n_r
    if state.log:
        state.log.debug(f'C->S [S-FRAME] N(R)={n_r}')


def datetime_to_cp56(dt: datetime, iv: bool = False) -> bytes:
    """Convert Python datetime to CP56Time2a format.

    CP56Time2a is a 7-byte time format used in IEC 104:
    - 2 bytes: milliseconds in current minute (little-endian)
    - 1 byte: minute + IV (invalid) flag
    - 1 byte: hour
    - 1 byte: day of month + day of week
    - 1 byte: month
    - 1 byte: year (last two digits)

    Args:
        dt: Python datetime object.
        iv: Invalid flag (sets bit 7 of minute byte).

    Returns:
        7-byte CP56Time2a representation.

    Example:
        >>> dt = datetime(2024, 1, 15, 14, 30, 25, 123000)
        >>> cp56 = datetime_to_cp56(dt)
        >>> len(cp56)
        7
    """
    # Milliseconds in current minute (little-endian)
    ms_total = (dt.second * 1000 + dt.microsecond // 1000) % 60000
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
    res[6] = (dt.year - 2000) & 0x7F
    return bytes(res)


def datetime_from_cp56(dt_bt: bytes) -> tuple[datetime | None, bool | None]:
    """Convert CP56Time2a format to Python datetime.

    Args:
        dt_bt: 7-byte CP56Time2a bytes.

    Returns:
        Tuple of (datetime, iv_flag) or (None, None) on error.

    Example:
        >>> cp56 = b'\\x7b\\x00\\x1e\\x0e\\x0f\\x01\\x18'
        >>> dt, iv = datetime_from_cp56(cp56)
        >>> print(dt)
        2024-01-15 14:30:25.123000
    """
    if len(dt_bt) < 7:
        raise ValueError('Insufficient data for CP56Time2a')
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
        dt = datetime(year, month, day, hour, mins, sec, microsecond=msec * 1000)
        return dt, iv
    except ValueError as e:
        return None, None


# ---- Decoding (client-side) ----

def _dec_val(type_id: int, data: bytes):
    """Decode value and quality from ASDU object data bytes."""
    if type_id in (1, 30, 45):
        return data[0] & 0x01, data[0] & 0xF0
    if type_id in (3, 31, 46):
        return data[0] & 0x03, data[0] & 0xF0
    if type_id in (5, 32):
        return struct.unpack('<b', bytes([data[0] & 0x7F]))[0], data[1]
    if type_id in (7, 33):
        return struct.unpack('<I', data[0:4])[0], data[4]
    if type_id in (9, 11, 34, 35):
        return struct.unpack('<h', data[0:2])[0], data[2]
    if type_id in (13, 36):
        return struct.unpack('<f', data[0:4])[0], data[4]
    if type_id in (15, 37):
        return struct.unpack('<i', data[0:4])[0], data[4]
    if type_id == 100:
        return data[0], 0
    return None, 0


def decode_i_frame_objects(frame: bytes) -> list[tuple]:
    """Decode objects from an I-frame.

    Returns:
        list of (ioa, type_id, value, quality, cot, coa, timestamp|None).
    """
    if len(frame) < 12:
        return []
    asdu = frame[6:]
    if len(asdu) < 6:
        return []
    type_id = asdu[0]
    count = asdu[1] & 0x7F
    is_seq = asdu[1] & 0x80
    cot = struct.unpack('<H', asdu[2:4])[0]
    coa = struct.unpack('<H', asdu[4:6])[0]
    val_size = const.ASDU_DATA_SIZE.get(type_id)
    if val_size is None:
        return []
    results = []
    offset = 6
    try:
        if is_seq:
            base_ioa = int.from_bytes(asdu[offset:offset+3], 'little')
            offset += 3
            for i in range(count):
                d = asdu[offset:offset+val_size]
                val, q = _dec_val(type_id, d)
                ts = datetime_from_cp56(d[val_size-7:])[0] if type_id in _TS_TYPES else None
                results.append((base_ioa + i, type_id, val, q, cot, coa, ts))
                offset += val_size
        else:
            for _ in range(count):
                ioa = int.from_bytes(asdu[offset:offset+3], 'little')
                offset += 3
                d = asdu[offset:offset+val_size]
                val, q = _dec_val(type_id, d)
                ts = datetime_from_cp56(d[val_size-7:])[0] if type_id in _TS_TYPES else None
                results.append((ioa, type_id, val, q, cot, coa, ts))
                offset += val_size
    except (IndexError, struct.error):
        pass
    return results


if __name__ == '__main__':
    b = datetime_to_cp56(datetime.now())
    print(b)
    c = datetime_from_cp56(b)
    print(c)