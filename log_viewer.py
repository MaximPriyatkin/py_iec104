"""
log_viewer.py - Log viewer for IEC 104 simulator.

Simple log viewer with real-time tailing and filtering capabilities.
No external dependencies, uses only Python standard library.
"""

import os
import sys
import time
import argparse
from pathlib import Path


def find_log_file():
    """Find default log file in current or parent directory."""
    # Look for srv.log in current directory
    if os.path.exists('srv.log'):
        return 'srv.log'
    # Look for KP_*/srv.log
    for d in os.listdir('.'):
        if os.path.isdir(d) and d.startswith('KP_'):
            log_path = os.path.join(d, 'srv.log')
            if os.path.exists(log_path):
                return log_path
    return None


def open_file(filename):
    """Open file for reading with error handling."""
    try:
        return open(filename, 'r', encoding='utf-8', errors='ignore')
    except (IOError, OSError):
        return None


def read_last_lines(file_obj, num_lines):
    """Read last N lines from file."""
    if not file_obj:
        return []

    try:
        # Get file size
        file_obj.seek(0, os.SEEK_END)
        size = file_obj.tell()

        if size == 0:
            return []

        # Read from the end
        lines = []
        block_size = 8192
        pos = size
        remaining = num_lines

        while pos > 0 and remaining > 0:
            read_size = min(pos, block_size)
            pos -= read_size
            file_obj.seek(pos)
            try:
                chunk = file_obj.read(read_size)
            except UnicodeDecodeError:
                # Skip bad bytes and continue
                continue

            lines_in_chunk = chunk.count('\n')
            remaining -= lines_in_chunk

        # Go to start of the block and read all
        if pos > 0:
            file_obj.seek(pos)
            file_obj.readline()  # Skip possible partial first line

        return file_obj.readlines()

    except (IOError, OSError):
        return []


def parse_line(line):
    """Parse log line: timestamp\tname\tlevel\tmessage"""
    line = line.strip()
    if not line:
        return None

    parts = line.split('\t')
    if len(parts) < 4:
        return None

    return {
        'timestamp': parts[0],
        'name': parts[1],
        'level': parts[2],
        'message': '\t'.join(parts[3:]),
    }


def should_show(entry, levels, module_filter):
    """Check if entry should be displayed."""
    if levels and entry['level'] not in levels:
        return False
    if module_filter and module_filter not in entry['name']:
        return False
    return True


def display_entry(entry):
    """Display log entry."""
    print(f"{entry['timestamp']}\t{entry['name']}\t{entry['level']}\t{entry['message']}")


def read_new_lines(file_obj, last_pos):
    """Read new lines from current position."""
    lines = []
    try:
        file_obj.seek(last_pos)
        for line in file_obj:
            lines.append(line)
        return lines, file_obj.tell()
    except UnicodeDecodeError:
        # If decode error, try to recover by reading byte by byte
        file_obj.seek(last_pos)
        data = file_obj.buffer.read()
        # Decode with ignore
        text = data.decode('utf-8', errors='ignore')
        lines = text.splitlines(keepends=True)
        return lines, file_obj.tell()
    except (IOError, OSError):
        return [], last_pos


def run_viewer(filename, follow, levels, module_filter, num_lines):
    """Main viewer loop."""
    file_obj = None
    last_size = 0

    if not filename:
        filename = find_log_file()
        if not filename:
            print("No log file found. Use: python log_viewer.py <logfile>")
            return
        print(f"Using default log file: {filename}")

    try:
        # Open file
        file_obj = open_file(filename)
        if not file_obj:
            print(f"Cannot open: {filename}")
            return

        # Show last N lines
        lines = read_last_lines(file_obj, num_lines)
        for line in lines:
            entry = parse_line(line)
            if entry and should_show(entry, levels, module_filter):
                display_entry(entry)

        if not follow:
            return

        # Follow mode
        print("\n--- Following log (Ctrl+C to stop) ---\n")

        # Remember current position
        try:
            file_obj.seek(0, os.SEEK_END)
            last_size = file_obj.tell()
        except (IOError, OSError):
            last_size = 0

        while True:
            try:
                # Check if file still exists
                if not os.path.exists(filename):
                    time.sleep(0.5)
                    continue

                current_size = os.path.getsize(filename)

                # If file got smaller, it was rotated - reopen
                if current_size < last_size:
                    if file_obj:
                        file_obj.close()
                    file_obj = open_file(filename)
                    if file_obj:
                        try:
                            file_obj.seek(0, os.SEEK_END)
                            last_size = file_obj.tell()
                        except (IOError, OSError):
                            last_size = 0
                    continue

                # Read new lines
                if current_size > last_size:
                    new_lines, last_size = read_new_lines(file_obj, last_size)
                    for line in new_lines:
                        entry = parse_line(line)
                        if entry and should_show(entry, levels, module_filter):
                            display_entry(entry)

                time.sleep(0.2)

            except (IOError, OSError):
                time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n")
    finally:
        if file_obj:
            file_obj.close()


def main():
    parser = argparse.ArgumentParser(
        description='View IEC 104 simulator logs',
        usage='%(prog)s [logfile] [options]'
    )
    parser.add_argument('logfile', nargs='?', default=None,
                       help='Path to log file (default: auto-detect)')
    parser.add_argument('-f', '--follow', action='store_true',
                       help='Follow log file (tail -f mode)')
    parser.add_argument('-n', '--lines', type=int, default=100,
                       help='Number of lines to show (default: 100)')
    parser.add_argument('-l', '--level', nargs='+',
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                       help='Show only specified levels')
    parser.add_argument('-m', '--module', help='Filter by module name')

    args = parser.parse_args()

    run_viewer(
        filename=args.logfile,
        follow=args.follow,
        levels=set(args.level) if args.level else None,
        module_filter=args.module,
        num_lines=args.lines,
    )


if __name__ == '__main__':
    main()