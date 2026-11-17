"""Rodar um processo com tempo limite real: no estouro, a árvore inteira é encerrada.

`subprocess.run` mata só o processo direto. Quando ele é um shell ou um
lançador (cmd.exe, node), o filho de verdade continua vivo segurando os pipes
e a espera vai até ele terminar sozinho.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading


class Cancelled(Exception):
    """A execução foi interrompida a pedido do usuário."""


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class CancelToken:
    """Sinal de interrupção de uma execução. Conhece o processo filho vivo, se houver."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Espera até `seconds`. Devolve True se a interrupção chegou nesse tempo."""
        return self._event.wait(seconds)

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            process = self._process
        if process is not None:
            _kill_tree(process)

    def attach(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._process = process
            already = self._event.is_set()
        if already:
            _kill_tree(process)

    def detach(self) -> None:
        with self._lock:
            self._process = None


def run_process(args, *, input=None, cwd=None, env=None, timeout=None, shell=False,
                cancel: CancelToken | None = None, **_ignored):
    """Mesmo contrato de `subprocess.run(..., capture_output=True, text=True)`, em UTF-8.

    Aceita e ignora os outros argumentos de `subprocess.run`, para poder ser
    trocado por ele (ou por um substituto de teste) sem mudar quem chama.
    """
    # Com `cancel`, lança Cancelled se a interrupção já foi pedida ou chegar enquanto
    # o processo roda; nesse caso a árvore do processo é encerrada antes.
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        shell=shell,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
    )
    if cancel is not None:
        cancel.attach(process)
    try:
        stdout, stderr = process.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        stdout, stderr = process.communicate()
        if cancel is not None and cancel.is_set():
            raise Cancelled() from None
        raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr) from None
    except BaseException:
        _kill_tree(process)
        process.wait()
        raise
    finally:
        if cancel is not None:
            cancel.detach()
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
