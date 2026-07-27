"""AIH Terminal — Web shell access (NO PASSWORD, local use only).

Adapted from CUI-Holaf-Utils/holaf_terminal.py by Holaf, 2025.

Differences vs. Holaf version:
  - NO password authentication: the WebSocket is open to anyone who
    can reach the ComfyUI server. Do not expose ComfyUI to a public
    network without putting it behind a reverse proxy with auth.
  - NO set-password / auth routes. The WebSocket just opens.
  - NO session tokens. Single-step connection.
  - Prefixed with [AIH] in all log output to avoid confusion with
    Holaf's terminal.

Routes registered (in /projects/AIH-keywords/__init__.py):
  - GET /aih/terminal  (WebSocket)
"""
import asyncio
import os
import platform
import shlex
import sys
import json
import traceback
import threading

from aiohttp import web

# Conditional imports for PTY
IS_WINDOWS = platform.system() == "Windows"
if not IS_WINDOWS:
    try:
        import pty, termios, tty, fcntl, select, struct
    except ImportError:
        print("🔴 [AIH-Terminal] Critical: pty/termios modules not found. Terminal will not work on non-Windows system.")
        pty = termios = tty = fcntl = select = struct = None
else:
    try:
        from winpty import PtyProcess
    except ImportError:
        print("🔴 [AIH-Terminal] Critical: 'pywinpty' is not installed. Terminal will not work on Windows.")
        print("   Please run 'pip install pywinpty' in your ComfyUI Python environment.")
        PtyProcess = None

# --- Terminal Environment ---
def is_running_in_conda():
    conda_prefix = os.environ.get('CONDA_PREFIX')
    return conda_prefix and sys.executable.startswith(os.path.normpath(conda_prefix))


# Default shell if no config is provided
DEFAULT_SHELL = os.environ.get('SHELL', '/bin/bash') if not IS_WINDOWS else 'cmd.exe'


async def websocket_handler(request: web.Request):
    """Open a PTY-backed shell session over WebSocket.

    NO AUTHENTICATION — anyone with network access to the ComfyUI port
    gets a shell. Localhost-only deployment is assumed.
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    print("🟢 [AIH-Terminal] WebSocket connection opened.")

    loop = asyncio.get_running_loop()
    pty_queue = asyncio.Queue()  # For data from PTY to WebSocket
    proc_adapter = None  # Will hold either WindowsPty or UnixPty instance

    try:
        user_shell = DEFAULT_SHELL
        shell_cmd_list = []
        current_env = os.environ.copy()

        if is_running_in_conda():
            conda_prefix = os.environ.get('CONDA_PREFIX')
            print(f"🔵 [AIH-Terminal] Running in a Conda environment: {conda_prefix}")
            if IS_WINDOWS:
                inner_cmd = f'call conda activate "{conda_prefix}" 2>nul && {user_shell}'
                shell_cmd_list = ['cmd.exe', '/K', inner_cmd]
            else:
                cmd_string = f'eval "$(conda shell.bash hook)" && conda activate "{conda_prefix}" && exec {user_shell}'
                shell_cmd_list = ['/bin/bash', '-c', cmd_string]
        else:
            # Cas general (venv, system python, etc.) : on se contente de
            # lancer le shell par defaut. Le PATH est herite du process
            # parent (ComfyUI), donc si l'utilisateur a lance ComfyUI
            # depuis un shell ou le venv/conda est active, `python` et
            # `pip` pointent vers le bon environnement. Le prompt du
            # shell n'affichera pas "(venv)" mais c'est un compromis
            # acceptable (coherent avec le comportement Holaf).
            print(f"🔵 [AIH-Terminal] Spawning shell: {user_shell} "
                  f"(VIRTUAL_ENV={os.environ.get('VIRTUAL_ENV', '<none>')})")
            shell_cmd_list = shlex.split(user_shell)
            # Cleanse Conda vars if present but not active, to avoid issues
            if 'CONDA_PREFIX' in current_env:
                print("🔵 [AIH-Terminal] Inherited Conda context detected. Cleansing environment.")
                for var_name in ['CONDA_PREFIX', 'CONDA_SHLVL', 'CONDA_DEFAULT_ENV', 'CONDA_PROMPT_MODIFIER']:
                    if var_name in current_env:
                        del current_env[var_name]

        print(f"🔵 [AIH-Terminal] Spawning shell with command: {shell_cmd_list}")

        if IS_WINDOWS:
            if not PtyProcess:
                await ws.close(code=1011, message=b'pywinpty library not found')
                return ws

            class WindowsPtyAdapter:
                def __init__(self, p): self.pty_proc = p
                def read(self, size): return self.pty_proc.read(size).encode('utf-8', errors='replace')
                def write(self, data_bytes): return self.pty_proc.write(data_bytes.decode('utf-8', errors='ignore'))
                def set_winsize(self, rows, cols): self.pty_proc.setwinsize(rows, cols)
                def is_alive(self): return self.pty_proc.isalive()
                def terminate(self, force=False): self.pty_proc.terminate(force)

            proc_adapter = WindowsPtyAdapter(PtyProcess.spawn(shell_cmd_list, dimensions=(24, 80), env=current_env))

        else:  # Linux/macOS
            if not pty:
                await ws.close(code=1011, message=b'pty/termios modules unavailable')
                return ws

            pid, fd = pty.fork()
            if pid == 0:  # Child process
                os.environ["TERM"] = "xterm"
                try:
                    os.execvpe(shell_cmd_list[0], shell_cmd_list, current_env)
                except FileNotFoundError:
                    os.execvpe("/bin/sh", ["/bin/sh"], current_env)
                sys.exit(1)

            class UnixPtyAdapter:
                def __init__(self, p, f_descriptor):
                    self.pid = p
                    self.fd = f_descriptor
                def read(self, size): return os.read(self.fd, size)
                def write(self, data_bytes): return os.write(self.fd, data_bytes)
                def set_winsize(self, rows, cols):
                    winsize = struct.pack('HHHH', rows, cols, 0, 0)
                    fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
                def is_alive(self):
                    try:
                        os.kill(self.pid, 0)
                        return True
                    except OSError:
                        return False
                def terminate(self, force=False):
                    try:
                        os.kill(self.pid, 15)  # SIGTERM
                    except ProcessLookupError:
                        pass

            # Set initial window size and terminal attributes for the PTY master
            initial_winsize_packed = struct.pack('HHHH', 24, 80, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, initial_winsize_packed)

            attrs = termios.tcgetattr(fd)
            attrs[3] &= ~termios.ICANON  # Disable canonical mode
            attrs[3] |= termios.ECHO     # Enable echo
            termios.tcsetattr(fd, termios.TCSANOW, attrs)

            proc_adapter = UnixPtyAdapter(pid, fd)

        # Thread to read from PTY and put data into asyncio queue
        def pty_reader_thread_target():
            try:
                while proc_adapter and proc_adapter.is_alive():
                    data = proc_adapter.read(1024)
                    if not data:
                        break
                    loop.call_soon_threadsafe(pty_queue.put_nowait, data)
            except (IOError, EOFError):
                pass
            finally:
                loop.call_soon_threadsafe(pty_queue.put_nowait, None)

        reader_thread = asyncio.to_thread(pty_reader_thread_target)

        async def pty_to_ws_sender():
            while True:
                data = await pty_queue.get()
                if data is None:
                    break
                try:
                    await ws.send_bytes(data)
                except ConnectionResetError:
                    break
            if not ws.closed:
                await ws.close()

        sender_task = asyncio.create_task(pty_to_ws_sender())

        async def ws_to_pty_receiver():
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data_json = json.loads(msg.data)
                        if 'resize' in data_json and isinstance(data_json['resize'], list) and len(data_json['resize']) == 2:
                            rows, cols = data_json['resize']
                            if proc_adapter:
                                proc_adapter.set_winsize(rows, cols)
                    except (json.JSONDecodeError, TypeError):
                        # Not JSON, assume it's direct input for the terminal
                        if proc_adapter:
                            proc_adapter.write(msg.data.encode('utf-8'))
                elif msg.type == web.WSMsgType.BINARY:
                    if proc_adapter:
                        proc_adapter.write(msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    print(f'🔴 [AIH-Terminal] WebSocket error: {ws.exception()}')
                    break

        receiver_task = asyncio.create_task(ws_to_pty_receiver())

        await asyncio.gather(sender_task, receiver_task, reader_thread)

    except Exception as e:
        print(f"🔴 [AIH-Terminal] Unhandled error in WebSocket PTY handler: {e}")
        traceback.print_exc()
    finally:
        print("⚫ [AIH-Terminal] Cleaning up PTY session.")
        if proc_adapter and proc_adapter.is_alive():
            proc_adapter.terminate(force=True)
        if not ws.closed:
            await ws.close()

    return ws
