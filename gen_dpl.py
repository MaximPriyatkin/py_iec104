#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DPL and CSV Generator for IEC 104 Simulator.

This module generates configuration files for the IEC 104 simulator:
- DPL (database dump) files for SCADA systems
- CSV signal configuration for the simulator

The generator creates signal definitions based on a template, supporting
multiple devices with configurable signal types.

Example:
    python generator.py --end 5 --type ZDV --template KP_1_ZDV_{} --output kp_1.dpl --signals-csv signals.csv
"""

import argparse
from dataclasses import dataclass
from typing import List
import const


@dataclass
class Signal:
    """Signal descriptor class for IEC 104 signal definition.

    Attributes:
        name: Signal name (e.g., "TU.Open")
        mek_type: IEC 104 type identifier (e.g., "45")
        direction: Signal direction: "output" (command) or "input" (monitoring)
    """
    name: str           # Signal name (e.g., "TU.Open")
    mek_type: str       # IEC 104 type (e.g., "45")
    direction: str      # Direction: "output" or "input"

    def get_direction_code(self) -> str:
        """Return the direction code for the driver.

        Returns:
            "\\5" for output commands, "\\2" for input monitoring.

        Example:
            >>> Signal("TU.Open", "45", "output").get_direction_code()
            '\\5'
        """
        return "\\5" if self.direction == "output" else "\\2"

    def get_driver_type(self) -> str:
        """Return the driver datatype based on IEC 104 type.

        Maps IEC 104 types to driver type codes:
        - 30, 31: Single point information -> 521
        - 36, 37: Measured value -> 526
        - 45, 46: Single/Double command -> 532
        - 50, 51: Setpoint -> 526
        - 58, 59: Step control -> 532

        Returns:
            Driver type code as string (default: "526").

        Example:
            >>> Signal("TU.Open", "45", "output").get_driver_type()
            '532'
        """
        type_map = {
            "30": "521",  # Type 30 - Single point information (TS)
            "31": "521",  # Type 31 - Single point information with timestamp (TS)
            "36": "526",  # Type 36 - Measured value (TI) - float
            "37": "526",  # Type 37 - Measured value with timestamp (TI)
            "45": "532",  # Type 45 - Single command (TU)
            "46": "532",  # Type 46 - Double command (TU)
            "50": "526",  # Type 50 - Setpoint (TR) - float
            "51": "526",  # Type 51 - Setpoint with timestamp (TR)
            "58": "532",  # Type 58 - Step control command (TU)
            "59": "532",  # Type 59 - Step control command with timestamp
        }
        return type_map.get(self.mek_type, "526")  # Default to 526 (measured value)


# Global list of signal definitions
SIGNALS: List[Signal] = [
    Signal("TU.ToOpen", "45", "output"),
    Signal("TU.ToClose", "45", "output"),
    Signal("TS.Opened", "30", "input"),
    Signal("TS.Closed", "30", "input"),
    Signal("TI.Pin", "36", "input"),
    Signal("TI.Pout", "36", "input"),
    Signal("TR.SetTimeOpen", "50", "output"),
    Signal("TR.SetTimeClose", "50", "output")
]


def ioa_to_bytes(ioa: int) -> str:
    """Convert numeric IOA to three-octet format.

    Converts an Information Object Address to the format used in DPL references:
    "high.middle.low" where:
    - high: most significant byte (third octet)
    - middle: middle byte (second octet)
    - low: least significant byte (first octet)

    Args:
        ioa: Information Object Address (1-16777215)

    Returns:
        String in format "high.middle.low"

    Example:
        >>> ioa_to_bytes(1)
        '0.0.1'
        >>> ioa_to_bytes(258)
        '0.1.2'
    """
    # Low byte (first octet in address)
    byte1 = ioa & 0xFF
    # Middle byte (second octet)
    byte2 = (ioa >> 8) & 0xFF
    # High byte (third octet) - usually not used for small addresses
    byte3 = (ioa >> 16) & 0xFF

    return f"{byte3}.{byte2}.{byte1}"


def generate_datapoint_section(type_name: str, name_template: str, start: int, end: int) -> str:
    """Generate the Datapoint/DpId section of the DPL file.

    Args:
        type_name: Type name for the datapoints
        name_template: Template for datapoint names with {} placeholder
        start: Starting counter value
        end: Ending counter value

    Returns:
        DPL section content as string

    Example:
        >>> generate_datapoint_section("ZDV", "KP_1_ZDV_{}", 1, 3)
        '\\n# Datapoint/DpId\\nDpName\\tTypeName\\tID\\nKP_1_ZDV_1\\tZDV\\t0\\n...'
    """
    lines = ["\n# Datapoint/DpId", "DpName\tTypeName\tID"]

    for i in range(start, end + 1):
        dp_name = name_template.format(i)
        lines.append(f"{dp_name}\t{type_name}\t0")

    return "\n".join(lines)


def generate_distribution_section(type_name: str, name_template: str, start: int, end: int, num_drv: str) -> str:
    """Generate the DistributionInfo section of the DPL file.

    Args:
        type_name: Type name for the datapoints
        name_template: Template for datapoint names with {} placeholder
        start: Starting counter value
        end: Ending counter value
        num_drv: Driver number

    Returns:
        DPL section content as string
    """
    lines = ["\n# DistributionInfo",
             "Manager/User\tElementName\tTypeName\t_distrib.._type\t_distrib.._driver"]

    for i in range(start, end + 1):
        dp_name = name_template.format(i)
        for signal in SIGNALS:
            lines.append(f"ASC (1)/0\t{dp_name}.{signal.name}\t{type_name}\t56\t\\{num_drv}")

    return "\n".join(lines)


def generate_periphaddr_section(type_name: str, name_template: str, start: int, end: int, ca: str) -> str:
    """Generate the PeriphAddrMain section of the DPL file.

    Args:
        type_name: Type name for the datapoints
        name_template: Template for datapoint names with {} placeholder
        start: Starting counter value
        end: Ending counter value
        ca: Common Address (e.g., "0.2")

    Returns:
        DPL section content as string
    """
    lines = ["\n# PeriphAddrMain",
             "Manager/User\tElementName\tTypeName\t_address.._type\t_address.._reference\t_address.._poll_group\t_address.._connection\t_address.._offset\t_address.._subindex\t_address.._direction\t_address.._internal\t_address.._lowlevel\t_address.._active\t_address.._start\t_address.._interval\t_address.._reply\t_address.._datatype\t_address.._drv_ident"]

    zero_date = "01.01.1970 00:00:00.000"

    # IOA counter (starts at 1 for the first signal)
    ioa_counter = 1

    for i in range(start, end + 1):
        dp_name = name_template.format(i)

        for signal in SIGNALS:
            # Convert IOA to three-octet format
            ioa_bytes = ioa_to_bytes(ioa_counter)

            # Format reference:
            # CLN2-{mek_type}.{ca}.{ioa_bytes}
            # where:
            # {ca} - fixed CA (Common Address) octets
            # {ioa_bytes} - IOA in high.middle.low format
            ref = f"\"CLN2-{signal.mek_type}.{ca}.{ioa_bytes}\""

            lines.append(f"ASC (1)/0\t{dp_name}.{signal.name}\t{type_name}\t16\t{ref}\t \t \t0\t0\t{signal.get_direction_code()}\t0\t0\t1\t{zero_date}\t{zero_date}\t{zero_date}\t{signal.get_driver_type()}\t\"IEC\"")

            # Increment IOA counter for the next signal
            ioa_counter += 1

    return "\n".join(lines)


def generate_signals_csv(ca_num: int, name_template: str, start: int, end: int) -> str:
    """Generate signals.csv content for the simulator.

    Creates CSV content with the same signal order and IOA mapping as the DPL file.

    Args:
        ca_num: Common Address number for the CSV
        name_template: Template for datapoint names with {} placeholder
        start: Starting counter value
        end: Ending counter value

    Returns:
        CSV content as string

    Note:
        The generated CSV matches the DPL file's IOA assignment order.
    """
    lines = ["id\tca\tioa\tasdu\tname\tdsc\tval\tthreshold"]
    sig_id = 1
    for i in range(start, end + 1):
        dp_name = name_template.format(i)
        for s in SIGNALS:
            asdu = int(s.mek_type)
            name = f"{dp_name}.{s.name}"
            is_float = asdu in const.FLOAT_ASDU
            val = "0.0" if is_float else "0"
            thresh = "0" if asdu in const.COMMAND_ASDU else ("0.1" if is_float else "")
            lines.append(f"{sig_id}\t{ca_num}\t{sig_id}\t{asdu}\t{name}\t\t{val}\t{thresh}")
            sig_id += 1
    return "\n".join(lines)


def main() -> None:
    """Main entry point for the DPL/CSV generator.

    Parses command-line arguments and generates configuration files:
    - DPL file for SCADA system import
    - Optional CSV file for the simulator

    The generator creates signal definitions for multiple devices with
    configurable naming patterns and address ranges.

    Example:
        python generator.py --end 5 --type ZDV --template KP_1_ZDV_{} \\
            --output kp_1.dpl --signals-csv signals.csv --ca 0.2 --ca-num 2
    """
    parser = argparse.ArgumentParser(description='Generate DPL database dump and CSV files for IEC 104 simulator')
    parser.add_argument('--type', default='ZDV', help='Element type (default: ZDV)')
    parser.add_argument('--template', default='KP_1_ZDV_{}', help='Name template with counter (default: KP_1_ZDV_{})')
    parser.add_argument('--start', type=int, default=1, help='Starting counter value (default: 1)')
    parser.add_argument('--end', '-e', type=int, required=True, help='Ending counter value')
    parser.add_argument('--output', '-o', default='kp_1.dpl', help='Output DPL file (default: output.txt)')
    parser.add_argument('--ca', '-c', default='0.2', help='Common Address (ca) for DPL reference (default: 0.2)')
    parser.add_argument('--ca-num', type=int, default=2, help='Common Address number for signals.csv (default: 2)')
    parser.add_argument('--drv', '-d', default='2', help='Default driver number (default: 2)')
    parser.add_argument('--signals-csv', '-s', default='signal.csv', help='Also generate signals.csv for simulator')

    args = parser.parse_args()

    # Validate range
    if args.start > args.end:
        print("Error: start value cannot be greater than end value")
        return

    # File header
    header = "# ascii dump of database\n"

    # Generate all sections
    sections = [
        header,
        generate_datapoint_section(args.type, args.template, args.start, args.end),
        generate_distribution_section(args.type, args.template, args.start, args.end, args.drv),
        generate_periphaddr_section(args.type, args.template, args.start, args.end, args.ca)
    ]

    # Combine all sections
    content = "\n".join(sections)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(content)

    if args.signals_csv:
        csv_content = generate_signals_csv(args.ca_num, args.template, args.start, args.end)
        with open(args.signals_csv, 'w', encoding='utf-8') as f:
            f.write(csv_content)
        print(f"File {args.signals_csv} (signals.csv) created.")

    # Calculate statistics
    signals_per_device = len(SIGNALS)
    total_signals = (args.end - args.start + 1) * signals_per_device
    last_ioa = total_signals

    print(f"File {args.output} successfully created!")
    print(f"Generated devices: {args.end - args.start + 1}")
    print(f"Total signals: {total_signals}")
    print(f"Type: {args.type}")
    print(f"Name template: {args.template}")
    print(f"Range: {args.start} - {args.end}")
    print(f"CA (Common Address): 0.2 (fixed)")
    print(f"IOA: sequential from 1 to {last_ioa}")

    # Display signal information
    print("\nSignal list:")
    for idx, signal in enumerate(SIGNALS, 1):
        direction_str = "OUTPUT" if signal.direction == "output" else "INPUT "
        print(f"  {idx:2d}. {signal.name:15} {direction_str} (IEC-{signal.mek_type} -> driver {signal.get_driver_type()})")

    # Show example IOA mappings for the first device
    print("\nExample signal mappings for the first device:")
    ioa_counter = 1
    for signal in SIGNALS:
        ioa_bytes = ioa_to_bytes(ioa_counter)
        print(f"  IOA {ioa_counter:2d} ({signal.name:15}) -> CLN2-{signal.mek_type}.0.2.{ioa_bytes} [{signal.get_direction_code()}]")
        ioa_counter += 1


if __name__ == "__main__":
    main()