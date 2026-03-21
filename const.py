"""
Constants for IEC 60870-5-104 protocol.

This module defines all constants, enumerations, and data structures used
in the IEC 104 protocol implementation including:
- Frame start byte and test frame constant
- U-frame type identifiers
- Cause of transmission (COT) codes
- ASDU type identifiers (monitoring, command, system information)
- ASDU data size lookup table
- ASDU classification sets (integer, float, command types)
"""

from enum import IntEnum
from dataclasses import dataclass

# IEC 104 Constants
START_BYTE = 0x68
TESTFR_ACT = b'\x68\x04\x43\x00\x00\x00'


class UTypeId(IntEnum):
    """U-frame type identifiers for control functions.

    U-frames are unnumbered frames used for connection management
    and testing.

    Attributes:
        STARTDT_ACT: Start data transfer activation (0x07)
        STARTDT_CON: Start data transfer confirmation (0x0B)
        STOPDT_ACT: Stop data transfer activation (0x13)
        STOPDT_CON: Stop data transfer confirmation (0x17)
        TESTFR_ACT: Test frame activation (0x43)
        TESTFR_CON: Test frame confirmation (0x83)
    """
    STARTDT_ACT = 0x07
    STARTDT_CON = 0x0B
    STOPDT_ACT = 0x13
    STOPDT_CON = 0x17
    TESTFR_ACT = 0x43
    TESTFR_CON = 0x83


# Complete confirmation frames (U-Format Responses)
# Format 104: [Start, Len, Type, 0, 0, 0]
U_RESP = {
    UTypeId.STARTDT_ACT: b'\x68\x04\x0B\x00\x00\x00',
    UTypeId.STOPDT_ACT:  b'\x68\x04\x17\x00\x00\x00',
    UTypeId.TESTFR_ACT:  b'\x68\x04\x83\x00\x00\x00',
}


class COT(IntEnum):
    """Cause of Transmission codes.

    Defines the reason for transmitting an ASDU.

    Attributes:
        PERIODIC: Cyclic transmission
        BACKGROUND: Background transmission
        SPONTANEOUS: Spontaneous transmission
        INITIALIZED: Initialized
        REQUEST: Request
        ACTIVATION: Activation
        DEACTIVATION: Deactivation
        ACTIVATION_CON: Activation confirmation
        DEACTIVATION_CON: Deactivation confirmation
        EXECUTED_CON: Execution confirmation
        ACTIVATION_TERM: General interrogation completion
        UNKNOWN_TYPE_ID: Unknown type identifier
    """
    PERIODIC = 1        # Cyclic transmission
    BACKGROUND = 2      # Background transmission
    SPONTANEOUS = 3     # Spontaneous transmission
    INITIALIZED = 4     # Initialized
    REQUEST = 5         # Request
    ACTIVATION = 6      # Activation
    DEACTIVATION = 8    # Deactivation
    ACTIVATION_CON = 7  # Activation confirmation
    DEACTIVATION_CON = 9  # Deactivation confirmation
    EXECUTED_CON = 10       # Execution confirmation
    ACTIVATION_TERM = 10    # General interrogation completion
    UNKNOWN_TYPE_ID = 44    # Unknown type identifier


class AsduTypeId(IntEnum):
    """ASDU type identifiers as defined in IEC 60870-5-104.

    Contains all standardized ASDU types for monitoring, command,
    system information, parameters, and file transfer.

    Note:
        Values are in decimal (as per IEC standard) not hex.
    """
    # ============= MONITORING DIRECTION =============
    # Basic types (without timestamp)
    M_SP_NA_1 = 0x01   # 1 Single point information
    M_DP_NA_1 = 0x03   # 2 Double point information
    M_ST_NA_1 = 0x05   # 5 Step position information
    M_BO_NA_1 = 0x07   # 7 Bitstring of 32 bits
    M_ME_NA_1 = 0x09   # 9 Measured value, normalized value
    M_ME_NB_1 = 0x0B   # 11 Measured value, scaled value
    M_ME_NC_1 = 0x0D   # 13 Measured value, floating point value
    M_IT_NA_1 = 0x0F   # 15 Integrated totals
    M_EP_TA_1 = 0x11   # 17 Protection event with timestamp
    M_EP_TB_1 = 0x12   # 18 Packed start events of protection
    M_EP_TC_1 = 0x13   # 19 Packed output circuit information of protection
    M_PS_NA_1 = 0x14   # 20 Packed single-point information
    M_ME_ND_1 = 0x15   # 21 Measured value, normalized value without quality descriptor

    # Types with CP56Time2a timestamp (7 bytes)
    M_SP_TB_1 = 0x1E   # 30 Single point with CP56Time2a timestamp
    M_DP_TB_1 = 0x1F   # 31 Double point with CP56Time2a timestamp
    M_ST_TB_1 = 0x20   # 32 Step position with CP56Time2a timestamp
    M_BO_TB_1 = 0x21   # 33 Bitstring of 32 bits with CP56Time2a timestamp
    M_ME_TD_1 = 0x22   # 34 Measured value, normalized with CP56Time2a timestamp
    M_ME_TE_1 = 0x23   # 35 Measured value, scaled with CP56Time2a timestamp
    M_ME_TF_1 = 0x24   # 36 Measured value, floating point with CP56Time2a timestamp
    M_IT_TB_1 = 0x25   # 37 Integrated totals with CP56Time2a timestamp
    M_EP_TD_1 = 0x26   # 38 Protection event with CP56Time2a timestamp
    M_EP_TE_1 = 0x27   # 39 Packed start events of protection with CP56Time2a timestamp
    M_EP_TF_1 = 0x28   # 40 Packed output circuit information of protection with CP56Time2a timestamp

    # ============= COMMAND DIRECTION =============
    # Commands (without timestamp)
    C_SC_NA_1 = 0x2D   # 45 Single command
    C_DC_NA_1 = 0x2E   # 46 Double command
    C_RC_NA_1 = 0x2F   # 47 Regulating step command
    C_SE_NA_1 = 0x30   # 49 Set point command, normalized value
    C_SE_NB_1 = 0x31   # 50 Set point command, scaled value
    C_SE_NC_1 = 0x32   # 51 Set point command, floating point value
    C_BO_NA_1 = 0x33   # 52 Bitstring of 32 bits command

    # Commands with CP56Time2a timestamp
    C_SC_TA_1 = 0x3A   # 58 Single command with CP56Time2a timestamp
    C_DC_TA_1 = 0x3B   # 59 Double command with CP56Time2a timestamp
    C_RC_TA_1 = 0x3C   # 60 Regulating step command with CP56Time2a timestamp
    C_SE_TA_1 = 0x3D   # 61 Set point command, normalized with CP56Time2a timestamp
    C_SE_TB_1 = 0x3E   # 62 Set point command, scaled with CP56Time2a timestamp
    C_SE_TC_1 = 0x3F   # 63 Set point command, floating point with CP56Time2a timestamp
    C_BO_TA_1 = 0x40   # 64 Bitstring of 32 bits command with CP56Time2a timestamp

    # ============= SYSTEM INFORMATION =============
    # Monitoring direction
    M_EI_NA_1 = 0x46   # 70 End of initialization

    # Command direction
    C_IC_NA_1 = 0x64   # 100 General interrogation command (100 decimal)
    C_CI_NA_1 = 0x65   # 101 Counter interrogation command
    C_RD_NA_1 = 0x66   # 102 Read command
    C_CS_NA_1 = 0x67   # 103 Clock synchronization command
    C_TS_NA_1 = 0x68   # 104 Test command
    C_RP_NA_1 = 0x69   # 105 Reset process command
    C_CD_NA_1 = 0x6A   # 106 Delay acquisition command
    C_TS_TA_1 = 0x6B   # 107 Test command with CP56Time2a timestamp

    # ============= PARAMETERS =============
    P_ME_NA_1 = 0x6E   # 110 Parameter, normalized value
    P_ME_NB_1 = 0x6F   # 111 Parameter, scaled value
    P_ME_NC_1 = 0x70   # 112 Parameter, floating point value
    P_AC_NA_1 = 0x71   # 113 Parameter activation

    # ============= FILE TRANSFER =============
    F_FR_NA_1 = 0x78   # 120 File ready
    F_SR_NA_1 = 0x79   # 121 Section ready
    F_SC_NA_1 = 0x7A   # 122 Call directory / select file
    F_LS_NA_1 = 0x7B   # 123 Last section / last segment
    F_AF_NA_1 = 0x7C   # 124 File / section acknowledgment
    F_SG_NA_1 = 0x7D   # 125 Segment
    F_DR_TA_1 = 0x7E   # 126 Directory


# ASDU types with integer values (discrete, single/double commands, etc.)
INT_ASDU = (
    AsduTypeId.M_SP_NA_1,
    AsduTypeId.M_DP_NA_1,
    AsduTypeId.M_SP_TB_1,
    AsduTypeId.M_DP_TB_1,
    AsduTypeId.C_SC_NA_1,
    AsduTypeId.C_DC_NA_1,
    AsduTypeId.C_RC_NA_1,
    AsduTypeId.C_SC_TA_1,
    AsduTypeId.C_DC_TA_1,
    AsduTypeId.C_RC_TA_1,
    AsduTypeId.C_BO_NA_1,
    AsduTypeId.C_BO_TA_1,
    AsduTypeId.C_IC_NA_1,
    AsduTypeId.C_CI_NA_1,
    AsduTypeId.C_RD_NA_1,
    AsduTypeId.C_CS_NA_1,
    AsduTypeId.C_TS_NA_1,
    AsduTypeId.C_RP_NA_1,
    AsduTypeId.C_CD_NA_1,
    AsduTypeId.C_TS_TA_1,
)


# ASDU types with floating point values (measured values)
FLOAT_ASDU = (
    AsduTypeId.M_ME_NC_1,
    AsduTypeId.M_ME_TF_1,
    AsduTypeId.C_SE_NA_1,
    AsduTypeId.C_SE_NB_1,
    AsduTypeId.C_SE_NC_1,
    AsduTypeId.C_SE_TA_1,
    AsduTypeId.C_SE_TB_1,
    AsduTypeId.C_SE_TC_1,
)


# Command ASDU types (no deadband, threshold=0)
COMMAND_ASDU = (
    AsduTypeId.C_SC_NA_1,
    AsduTypeId.C_DC_NA_1,
    AsduTypeId.C_RC_NA_1,
    AsduTypeId.C_SE_NA_1,
    AsduTypeId.C_SE_NB_1,
    AsduTypeId.C_SE_NC_1,
    AsduTypeId.C_BO_NA_1,
    AsduTypeId.C_SC_TA_1,
    AsduTypeId.C_DC_TA_1,
    AsduTypeId.C_RC_TA_1,
    AsduTypeId.C_SE_TA_1,
    AsduTypeId.C_SE_TB_1,
    AsduTypeId.C_SE_TC_1,
    AsduTypeId.C_BO_TA_1,
    AsduTypeId.C_IC_NA_1,
    AsduTypeId.C_CI_NA_1,
    AsduTypeId.C_RD_NA_1,
    AsduTypeId.C_CS_NA_1,
    AsduTypeId.C_TS_NA_1,
    AsduTypeId.C_RP_NA_1,
    AsduTypeId.C_CD_NA_1,
    AsduTypeId.C_TS_TA_1,
)


# Object data length (Value + Quality + Timestamp) WITHOUT the 3-byte IOA
ASDU_DATA_SIZE = {
    # --- Without timestamp ---
    1:  1,  # M_SP_NA_1: 1 byte (SIQ)
    3:  1,  # M_DP_NA_1: 1 byte (DIQ)
    13: 5,  # M_ME_NC_1: 4 bytes (float) + 1 byte (QDS)
    45: 1,  # C_SC_NA_1: 1 byte (SCO)
    50: 5,  # C_SE_NC_1: 4 bytes (float) + 1 byte (QDS)

    # --- With CP56Time2a timestamp (7 bytes) ---
    30: 8,  # M_SP_TB_1: 1 byte (SIQ) + 7 bytes (Time)
    31: 8,  # M_DP_TB_1: 1 byte (DIQ) + 7 bytes (Time)
    36: 12, # M_ME_TF_1: 4 bytes (float) + 1 byte (QDS) + 7 bytes (Time)
}