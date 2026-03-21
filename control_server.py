"""
control_server.py - Command handler for IEC 104 server.

This module implements the command-line interface for controlling:
- IEC 104 server (controller simulator)

Commands are entered in the console after starting the server.

Examples:
    # Start server
    python main.py --server
    
    > clients              # Show connected clients
    > set 45 100.5         # Set signal value
    > imit_rand 60 10      # Start random simulation
    > help set             # Detailed help for set command
"""

from threading import Thread
from types import SimpleNamespace
from typing import Callable

import common as cm
import imit as im


def _cmd_exit(ctx, _args):
    """
    exit - Stop the server.

    Terminates the server, closes all connections, and exits.

    Example:
        > exit
    """
    ctx.log.info('Stopping server')
    ctx.stop_thread.set()
    return True


def _cmd_clients(ctx, _args):
    """
    clients - Show list of connected clients.

    Displays information about each connected client:
    - IP address and port
    - STARTDT status
    - Send/receive frame counters

    Example:
        > clients
        ('192.168.1.100', 2404) startdt=True send_sq=15 rec_sq=12
    """
    for addr, state in ctx.cl.get_clients().items():
        print(addr, state)


def _cmd_addr(ctx, args):
    """
    addr <signal_name> - Show signal value by name.

    Arguments:
        signal_name : Signal name from configuration

    Example:
        > addr pressure_1
        IOA:45 VALUE:100.50 QUAL:0 TS:2024-01-15 10:23:45
    """
    cm.print_signals(ctx.sg.get_signal_by_name(args[0]))


def _cmd_set(ctx, args):
    """
    set <value> <id> [quality] - Set signal by ID.

    Arguments:
        value   : Numeric value (float)
        id      : Signal ID (database number)
        quality : Optional, decimal (default 0)
                  0=good, 128=invalid, 64=not topical, 16=blocked, 32=substituted

    Example:
        > set 100.5 45
        > set 100.5 45 128
    """
    q = int(args[2]) if len(args) > 2 else 0
    res = ctx.sg.update_val(float(args[0]), id=int(args[1]), q=q)
    if res:
        cm.print_signals(ctx.sg.get_signal(int(args[1])))


def _cmd_setioa(ctx, args):
    """
    setioa <value> <ioa> - Set signal by IOA.

    Arguments:
        value : Numeric value (float)
        ioa   : Information Object Address (1-65535)

    Example:
        > setioa 100.5 45
        OK: IOA 45 = 100.5

    Note:
        Automatically finds signal by IOA.
    """
    res = ctx.sg.update_val(float(args[0]), ioa=int(args[1]))
    if res:
        cm.print_signals(ctx.sg.get_all())


def _cmd_imit_rand(ctx, args):
    """
    imit_rand <cnt_time> <cnt_id> - Start random simulation.

    Arguments:
        cnt_time : Number of time iterations
        cnt_id   : Number of signals to simulate

    Generates random values for signals in range ID 5..100 with step 8.

    Example:
        > imit_rand 60 10
        Simulation started in background

    Note:
        Simulation runs in a background thread.
    """
    cnt_time, cnt_id = int(args[0]), int(args[1])

    def run():
        list_id = list(range(5, 100, 8))
        print(list_id)
        for _, sid, val, q in im.imit_rand(cnt_time=cnt_time, cnt_id=cnt_id, list_id=list_id, sleep_s=im.SIM_SLEEP):
            ctx.sg.update_val(val, id=sid, q=q)
        ctx.log.info('Simulation finished')

    Thread(target=run, daemon=True).start()
    print('Simulation started in background')


def _cmd_imit_ladder(ctx, args):
    """
    imit_ladder <cnt_step> <time_step> <val_step> <val_min> <val_max> <name_sg> - Start ladder simulation.

    Arguments:
        cnt_step  : Number of steps
        time_step : Time between steps (seconds)
        val_step  : Value increment per step
        val_min   : Minimum value
        val_max   : Maximum value
        name_sg   : Signal name to simulate

    Gradually changes signal value within specified range.

    Example:
        > imit_ladder 100 0.5 1.0 0 100 pressure_1
        Simulation started in background for 1 signals

    Note:
        Signal must be analog (ASDU=36).
    """
    cnt_step = int(args[0])
    time_step, val_step, val_min, val_max = float(args[1]), float(args[2]), float(args[3]), float(args[4])
    signals = ctx.sg.get_signal_by_name(args[5])
    list_id = [key for key, sg in signals.items() if sg.asdu == 36]
    if not list_id:
        print('No matching analog signals (ASDU=36) found')
        return

    def run():
        for _, sid, val, q in im.imit_ladder(
            cnt_step=cnt_step,
            time_step=time_step,
            val_step=val_step,
            val_min=val_min,
            val_max=val_max,
            list_id=list_id,
        ):
            ctx.sg.update_val(val, id=sid, q=q)
        ctx.log.info('Simulation finished')

    Thread(target=run, daemon=True).start()
    print(f'Simulation started in background for {len(list_id)} signals')


def _cmd_set_log_level(ctx, args):
    """
    log_level <target> <level> - Set logging level.

    Arguments:
        target : 'file' or 'console'
        level  : DEBUG, INFO, WARNING, ERROR, CRITICAL

    Example:
        > log_level console DEBUG
        CONSOLE level changed to DEBUG for all

    Note:
        Sets the level for all log handlers.
    """
    target = args[0].lower()
    level_str = args[1].upper()
    level_int = getattr(cm.logging, level_str, None)
    if level_int is None or target not in ('file', 'console'):
        return

    logger = ctx.log
    for hdl in logger.handlers:
        if target == 'file' and isinstance(hdl, cm.logging.FileHandler):
            hdl.setLevel(level_str)
            print(f"FILE level changed to {level_str}")
        elif target == 'console' and type(hdl) is cm.logging.StreamHandler:
            hdl.setLevel(level_str)
            print(f"CONSOLE level changed to {level_str}")


def _cmd_help(ctx, _args):
    """
    help - Show list of available commands.

    Displays all commands with argument count indicators.

    For detailed help on a specific command, use:
        help <command>

    Example:
        > help
          exit
          clients
          set <arg1> <arg2> ...
          ...

        > help set
    """
    print("\n=== Available server commands ===\n")
    for name, (n, _) in COMMANDS.items():
        print(f"  {name}" + (f" <arg1> <arg2> ..." if n else ""))
    print("\nFor command help: help <command>\n")


COMMANDS = {
    "exit": (0, _cmd_exit),
    "clients": (0, _cmd_clients),
    "addr": (1, _cmd_addr),
    "set": (2, _cmd_set),
    "setioa": (2, _cmd_setioa),
    "imit_rand": (2, _cmd_imit_rand),
    "imit_ladder": (6, _cmd_imit_ladder),
    "log_level": (2, _cmd_set_log_level),
    "help": (0, _cmd_help),
}


def server_handler(stop_thread: Callable, cl: Callable, sg: Callable, log, prompt_id: str = "KP ?"):
    """
    Command-line handler for the server.

    Runs an infinite loop reading commands from stdin and executing them.
    Supports commands from COMMANDS dictionary and help <command>.

    Args:
        stop_thread: threading.Event to stop the loop
        cl: Client storage object (client_storage)
        sg: Signal storage object (data_storage)
        log: Logger instance
        prompt_id: Identifier for the input prompt (default: "KP ?")
    """
    ctx = SimpleNamespace(stop_thread=stop_thread, cl=cl, sg=sg, log=log)
    prompt = f"Server KP_{prompt_id}> "
    while not stop_thread.is_set():
        try:
            line = input(prompt).strip().lower()
        except EOFError:
            log.info('Input closed, stopping server')
            stop_thread.set()
            return
        except Exception as e:
            log.exception('Input error: %s', e)
            continue
        if not line:
            continue
        parts = line.split()
        cmd_name, args = parts[0], parts[1:]
        if cmd_name == 'help' and args:
            cmd_help = args[0]
            if cmd_help in COMMANDS:
                _, handler = COMMANDS[cmd_help]
                print(handler.__doc__ or f"Help for {cmd_help} not found")
            else:
                print(f"Unknown command: {cmd_help}")
            continue
        entry = COMMANDS.get(cmd_name)
        if entry is None:
            log.info('Unknown command: %s', cmd_name)
            print('Unknown command. help — list of commands.')
            continue
        n_args, handler = entry
        if len(args) < n_args:
            print(f'Expected at least {n_args} args for {cmd_name}, got {len(args)}. help — list of commands.')
            continue
        try:
            if handler(ctx, args):
                return
        except Exception as e:
            log.exception('Error executing command %s: %s', cmd_name, e)
            print('Error:', e)