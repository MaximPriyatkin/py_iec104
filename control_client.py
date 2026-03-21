"""
control_client.py - Command handler for IEC 104 client.

This module implements the command-line interface for the client side:
- Connecting to remote controllers (IEC 104 servers)
- Sending STARTDT, general interrogation (GI)
- Session management

Commands are entered in the console after starting the client.

Examples:
    # Start client
    cd PU_1
    python ../client.py
    
    > conn plc1 192.168.1.10 2404 1   # Connect to controller
    > clients                          # Show active connections
    > start plc1                       # Send STARTDT
    > gi plc1                          # Send general interrogation
    > disc plc1                        # Disconnect
"""

from types import SimpleNamespace
from typing import Callable


def _cmd_exit(ctx, _args):
    """
    exit - Stop the client.

    Terminates the client, closes all connections, and exits.

    Example:
        > exit
    """
    ctx.log.info('Stopping client')
    ctx.stop_thread.set()
    return True


def _cmd_clients(ctx, _args):
    """
    clients - Show list of active connections.

    Displays information about each connection:
    - Connection name
    - Controller IP address and port
    - STARTDT status (confirmed or not)
    - V(S) and V(R) counters for synchronization checking

    Example:
        > clients
        plc1 ('192.168.1.10', 2404) startdt=True V(S)=15 V(R)=12

    Note:
        If no active connections, a message is displayed.
    """
    sessions = ctx.api.list_sessions()
    if len(sessions) == 0:
        print('No active connections')
        return
    for name, (state, _) in sessions.items():
        print(name, state.addr, f"startdt={state.startdt_confirmed}", f"V(S)={state.send_sq}", f"V(R)={state.rec_sq}")


def _cmd_conn(ctx, args):
    """
    conn <name> <ip> <port> <ca> - Create a new connection to a controller.

    Arguments:
        name : Connection name (for referencing in other commands)
        ip   : Controller IP address (IEC 104 server)
        port : Port (usually 2404)
        ca   : Common Address (station address, usually 1)

    Example:
        > conn plc1 192.168.1.10 2404 1
        Connected: plc1 -> 192.168.1.10:2404 ca=1

    Note:
        Connection is created but no data is transferred until STARTDT is sent.
    """
    name, ip, port, ca = args
    ctx.api.connect(name, ip, int(port), int(ca))
    print(f"Connected: {name} -> {ip}:{port} ca={ca}")


def _cmd_disc(ctx, args):
    """
    disc <name> - Disconnect a connection.

    Arguments:
        name : Connection name

    Example:
        > disc plc1
        Disconnected: plc1

    Note:
        Closes the TCP connection and removes the session.
    """
    name = args[0]
    ctx.api.disconnect(name)
    print(f"Disconnected: {name}")


def _cmd_start(ctx, args):
    """
    start <name> - Send STARTDT to begin data transmission.

    Arguments:
        name : Connection name

    Example:
        > start plc1
        STARTDT sent: plc1

    Note:
        After STARTDT confirmation, the controller will start transmitting data.
        Without STARTDT, the controller will not send actual values.
    """
    name = args[0]
    ctx.api.startdt(name)
    print(f"STARTDT sent: {name}")


def _cmd_gi(ctx, args):
    """
    gi <name> - Send general interrogation command.

    Arguments:
        name : Connection name

    Example:
        > gi plc1
        GI sent: plc1

    Note:
        The controller should respond with all current signal values.
        Required for state synchronization after connection.
    """
    name = args[0]
    ctx.api.gi(name)
    print(f"GI sent: {name}")


def _cmd_load(ctx, _args):
    """
    load - Load connections from config.toml [[conn]] and auto-connect.

    Reads connection definitions, connects, sends STARTDT and GI
    for each defined connection (if auto_start / auto_gi are set).

    Example:
        > load
        Connected: kp1 -> 127.0.0.1:2404 ca=2
    """
    connections = ctx.api.load_config()
    if not connections:
        print('No [[conn]] sections found in config.toml')
        return
    for c in connections:
        try:
            ctx.api.connect(c.name, c.ip, c.port, c.ca)
            if c.auto_start:
                ctx.api.startdt(c.name)
            if c.auto_gi:
                ctx.api.gi(c.name)
            print(f"Connected: {c.name} -> {c.ip}:{c.port} ca={c.ca}")
        except Exception as e:
            print(f"Error {c.name}: {e}")


def _cmd_help(ctx, _args):
    """
    help - Show list of available client commands.

    Displays all commands with argument count indicators.

    For detailed help on a specific command, use:
        help <command>

    Example:
        > help
          exit
          clients
          conn <arg1> <arg2> ...
          ...

        > help conn
    """
    print("\n=== Available client commands ===\n")
    for name, (n, _) in CLIENT_COMMANDS.items():
        print(f"  {name}" + (f" <arg1> <arg2> ..." if n else ""))
    print("\nFor command help: help <command>\n")


CLIENT_COMMANDS = {
    "exit": (0, _cmd_exit),
    "clients": (0, _cmd_clients),
    "conn": (4, _cmd_conn),
    "disc": (1, _cmd_disc),
    "start": (1, _cmd_start),
    "gi": (1, _cmd_gi),
    "load": (0, _cmd_load),
    "help": (0, _cmd_help),
}


def client_handler(stop_thread: Callable, api: Callable, log, prompt_id: str = "client"):
    """
    Command-line handler for the client.

    Runs an infinite loop reading commands from stdin and executing them.
    Supports commands from CLIENT_COMMANDS dictionary and help <command>.

    Args:
        stop_thread: threading.Event to stop the loop
        api: API object for connection management (connect, disconnect, startdt, gi)
        log: Logger instance
        prompt_id: Identifier for the input prompt (default: "client")

    Example:
        >>> client_handler(stop_event, api_instance, logger, "plc")
        plc > conn device1 192.168.1.10 2404 1
    """
    ctx = SimpleNamespace(stop_thread=stop_thread, api=api, log=log)
    prompt = f"{prompt_id} > "
    while not stop_thread.is_set():
        try:
            line = input(prompt).strip().lower()
        except EOFError:
            log.info('Input closed, stopping client')
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
            if cmd_help in CLIENT_COMMANDS:
                _, handler = CLIENT_COMMANDS[cmd_help]
                print(handler.__doc__ or f"Help for {cmd_help} not found")
            else:
                print(f"Unknown command: {cmd_help}")
            continue
        entry = CLIENT_COMMANDS.get(cmd_name)
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