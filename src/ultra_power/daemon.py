#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""One power coordinator: ephemeral ULTRA, PAM approval and transactional recovery."""

import json
import logging
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import threading
import time
import dbus
from typing import Any, Literal
from schema import Config, config as parse_config
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop, threads_init
from gi.repository import GLib
from hardware import Hardware, STATE, RUNTIME
from gpu import consumers, terminate_reviewed, runtime_state, warning

NAME = "io.github.vcanonici.UltraPower"
PATH = "/io/github/vcanonici/UltraPower"
IFACE = NAME
PROPS = "org.freedesktop.DBus.Properties"
HERE = Path(__file__).resolve().parent
logging.basicConfig(level=logging.INFO, format="%(message)s")


class Error(dbus.DBusException):
    _dbus_error_name = NAME + ".Error"


class Service(dbus.service.Object):
    def __init__(self, bus: Any, config: Config) -> None:
        self.bus = bus
        self.config = config
        self.hardware = Hardware(config)
        self.active = False
        self.pending = False
        self.applying = False
        self.preset = "minimal"
        self.error = ""
        self.message = ""
        self.owner: str | None = None
        self.child: subprocess.Popen[bytes] | None = None
        self.epoch = 0
        self.restore_requested: str | None = None
        self.sleeping = False
        name = dbus.service.BusName(NAME, bus=bus)
        super().__init__(name, PATH)
        bus.add_signal_receiver(
            self.source_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS,
            bus_name="org.freedesktop.UPower",
            path="/org/freedesktop/UPower",
        )
        bus.add_signal_receiver(
            self.profile_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS,
            bus_name="net.hadess.PowerProfiles",
            path="/net/hadess/PowerProfiles",
        )
        bus.add_signal_receiver(
            self.sleep_changed,
            signal_name="PrepareForSleep",
            dbus_interface="org.freedesktop.login1.Manager",
            path="/org/freedesktop/login1",
        )
        bus.add_signal_receiver(
            self.owner_changed,
            signal_name="NameOwnerChanged",
            dbus_interface="org.freedesktop.DBus",
        )
        GLib.idle_add(self.startup)

    def state(self) -> dict[str, bool | str]:
        gpu_state = runtime_state()
        return {
            "Active": dbus.Boolean(self.active),
            "Pending": dbus.Boolean(self.pending),
            "Applying": dbus.Boolean(self.applying),
            "CpuPreset": self.preset,
            "LastError": self.error,
            "ApprovalMessage": self.message,
            "GpuRuntimeState": gpu_state,
            "GpuWarning": warning(self.active, gpu_state),
        }

    def notify(self) -> None:
        self.PropertiesChanged(IFACE, self.state(), [])

    def check(self, sender: str | None, recovery: bool = False) -> None:
        if sender is None:
            raise Error("Remetente ausente.")
        uid = int(self.bus.get_unix_user(sender))
        if uid != int(self.config["uid"]) and not (recovery and uid == 0):
            raise Error("Usuario nao autorizado.")

    @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str) -> dict[str, bool | str]:
        if interface != IFACE:
            raise Error("Interface desconhecida.")
        return self.state()

    @dbus.service.method(PROPS, in_signature="ss", out_signature="v")
    def Get(self, interface: str, key: str) -> bool | str:
        return self.GetAll(interface)[key]

    @dbus.service.signal(PROPS, signature="sa{sv}as")
    def PropertiesChanged(
        self, interface: str, values: dict[str, bool | str], invalidated: list[str]
    ) -> None:
        pass

    @dbus.service.method(
        IFACE, in_signature="", out_signature="a(ssb)", sender_keyword="sender"
    )
    def GetBlockers(self, sender: str | None = None) -> list[tuple[str, str, bool]]:
        self.check(sender)
        if not self.hardware.battery():
            raise Error("Desconecte o carregador para ativar o ULTRA.")
        if not self.hardware.local_session():
            raise Error("Exige sessão local desbloqueada.")
        return consumers(int(self.config["uid"]))

    def _validate_request(
        self,
        force: bool,
        reviewed: list[str] | tuple[str, ...],
        sender: str | None,
        replace_pending: bool = False,
    ) -> tuple[str, ...]:
        self.check(sender)
        if self.active or self.applying or STATE.exists() or (self.pending and (not replace_pending or self.owner != sender)):
            raise Error("Operacao em curso ou recuperacao pendente.")
        if (
            not self.hardware.battery()
        ) or not self.hardware.local_session():
            raise Error("Exige bateria e sessao local desbloqueada.")
        blockers = consumers(int(self.config["uid"]))
        if force and (
            set(map(str, reviewed)) != {row[0] for row in blockers}
            or any(not row[2] for row in blockers)
        ):
            raise Error(
                "Lista de processos mudou ou inclui servidor grafico; cancelado."
            )
        return tuple(map(str, reviewed))

    @dbus.service.method(IFACE, in_signature="bas", out_signature="", sender_keyword="sender")
    def ConfirmEnable(self, force: bool = False, reviewed: list[str] | tuple[str, ...] = (), sender: str | None = None) -> None:
        """Explicit red-OK alternative, under the same local-session safeguards."""
        tokens = self._validate_request(force, reviewed, sender, replace_pending=True)
        if self.pending:
            self.cancel()
        self.epoch += 1
        self.owner = sender
        self.error = ""
        self.message = ""
        self.job("enter", token=self.epoch, reviewed=tokens if force else None)

    @dbus.service.method(IFACE, in_signature="bas", out_signature="", sender_keyword="sender")
    def RequestEnable(self, force: bool = False, reviewed: list[str] | tuple[str, ...] = (), sender: str | None = None) -> None:
        reviewed = self._validate_request(force, reviewed, sender)
        self.epoch += 1
        token = self.epoch
        self.owner = sender
        self.error = ""
        self.pending = True
        self.message = "Coloque o dedo cadastrado no leitor ou confirme no OK vermelho."
        username = pwd.getpwuid(int(self.config["uid"])).pw_name
        self.child = subprocess.Popen(
            ["/usr/bin/python3", str(HERE / "pam_finger.py"), username],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        started = time.monotonic()
        last_session_check = started
        self.notify()

        def poll() -> bool:
            nonlocal last_session_check
            if token != self.epoch:
                return False
            if time.monotonic() - last_session_check >= 1:
                last_session_check = time.monotonic()
                if not self.hardware.local_session():
                    self.cancel()
                    return False
            assert self.child is not None
            result = self.child.poll()
            if result is None:
                if time.monotonic() - started > 30:
                    self.cancel("Tempo de aprovacao esgotado.")
                    return False
                return True
            assert self.child.stdout is not None
            self.child.stdout.close()
            self.child = None
            self.pending = False
            self.message = ""
            if result != 0:
                self.error = "Digital nao aprovada. Nenhum ajuste foi aplicado."
                self.notify()
            elif not self.hardware.local_session() or (
                not self.hardware.battery()
            ):
                self.error = "Estado mudou durante a aprovacao. Tente novamente."
                self.notify()
            else:
                self.job("enter", token=token, reviewed=reviewed if force else None)
            return False

        GLib.timeout_add(100, poll)

    def cancel(self, message: str = "") -> None:
        self.epoch += 1
        if self.child:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait()
            assert self.child.stdout is not None
            self.child.stdout.close()
            self.child = None
        self.pending = False
        self.owner = None
        self.message = ""
        self.error = message
        self.notify()

    @dbus.service.method(
        IFACE, in_signature="", out_signature="", sender_keyword="sender"
    )
    def Cancel(self, sender: str | None = None) -> None:
        self.check(sender)
        if self.pending and sender == self.owner:
            self.cancel()
        elif self.applying and sender == self.owner:
            self.epoch += 1
            self.restore_requested = ""

    @dbus.service.method(
        IFACE, in_signature="s", out_signature="", sender_keyword="sender"
    )
    def Disable(self, target: str = "", sender: str | None = None) -> None:
        logging.info("ULTRA: saida solicitada; perfil=%s", target or "automatico")
        self.check(sender, recovery=True)
        if target not in ("", "performance", "balanced", "power-saver"):
            raise Error("Perfil invalido.")
        if self.pending:
            self.cancel()
        if self.applying:
            self.epoch += 1
            self.restore_requested = target
            return
        if self.active or STATE.exists():
            self.job("restore", target=target or None)
        elif target:
            self.hardware.profile(target)

    @dbus.service.method(
        IFACE, in_signature="s", out_signature="", sender_keyword="sender"
    )
    def SetPreset(self, preset: str, sender: str | None = None) -> None:
        self.check(sender)
        if preset not in self.config["presets"] or not self.active or self.applying:
            raise Error("Preset indisponivel.")
        self.job("preset", preset=str(preset))

    def job(
        self,
        action: Literal["enter", "restore", "preset"],
        token: int | None = None,
        target: str | None = None,
        preset: str | None = None,
        reviewed: tuple[str, ...] | None = None,
    ) -> None:
        if self.applying:
            return
        logging.info("ULTRA: operacao=%s", action)
        self.applying = True
        self.notify()

        def work() -> None:
            failure = ""
            try:
                hw = Hardware(self.config)
                if action == "enter":
                    if (
                        token != self.epoch
                        or not hw.local_session()
                        or (not hw.battery())
                    ):
                        raise RuntimeError(
                            "Ativacao cancelada antes de alterar processos ou hardware."
                        )
                    if reviewed is not None:
                        terminate_reviewed(int(self.config["uid"]), reviewed)
                        # Bounded wait for kernel descriptors to be released.
                        for _ in range(20):
                            if not consumers(int(self.config["uid"])):
                                break
                            time.sleep(0.1)
                    hw.enter()
                    if token != self.epoch or (
                        not hw.battery()
                    ):
                        hw.restore()
                        raise RuntimeError("Ativacao cancelada por mudanca de estado.")
                elif action == "restore":
                    hw.restore(target)
                elif action == "preset":
                    assert preset is not None
                    try:
                        hw.cpus(preset)
                    except Exception:
                        hw.restore()
                        raise
            except Exception as e:
                failure = str(e)
                logging.error("%s: %s", action, type(e).__name__)
            GLib.idle_add(self.done, action, failure, preset)

        threading.Thread(target=work, daemon=True).start()

    def done(self, action: str, failure: str, preset: str | None) -> bool:
        self.applying = False
        self.error = failure
        if action == "enter":
            self.active = not bool(failure)
            self.preset = "minimal"
        elif action == "restore":
            self.active = False
        elif action == "preset":
            if failure:
                self.active = False
            else:
                assert preset is not None
                self.preset = preset
        self.notify()
        if self.restore_requested is not None:
            target = self.restore_requested
            self.restore_requested = None
            self.job("restore", target=target or None)
        elif failure and STATE.exists():
            GLib.timeout_add_seconds(20, self.retry_recovery)
        return False

    def retry_recovery(self) -> bool:
        if STATE.exists() and not self.applying:
            self.job("restore")
        return False

    def startup(self) -> bool:
        # No persisted preference can activate ULTRA at process start or boot.
        RUNTIME.unlink(missing_ok=True)
        if STATE.exists():
            self.job("restore")
        else:
            self.normal()
        return False

    def normal(self) -> None:
        if not self.active and not self.applying:
            try:
                self.hardware.profile(
                    "power-saver" if self.hardware.battery() else "performance"
                )
            except dbus.DBusException:
                logging.error("Perfil normal indisponivel.")

    def source_changed(
        self, interface: str, values: dict[str, Any], invalidated: list[str]
    ) -> None:
        if interface == "org.freedesktop.UPower" and (
            "OnBattery" in values or "OnBattery" in invalidated
        ):
            logging.info("ULTRA: fonte alterada; bateria=%s", self.hardware.battery())
            if not self.hardware.battery():
                if self.pending:
                    self.cancel()
                if self.applying:
                    self.epoch += 1
                    self.restore_requested = "performance"
                elif self.active:
                    self.job("restore", target="performance")
                else:
                    self.normal()
            elif not self.active:
                self.normal()

    def profile_changed(
        self, interface: str, values: dict[str, Any], invalidated: list[str]
    ) -> None:
        if (
            self.active
            and not self.applying
            and "ActiveProfile" in values
            and str(values["ActiveProfile"]) != "power-saver"
        ):
            logging.info("ULTRA: perfil externo=%s", str(values["ActiveProfile"]))
            self.job("restore", target=str(values["ActiveProfile"]))

    def owner_changed(self, name: str, old: str, new: str) -> None:
        if name == self.owner and not new:
            logging.info("ULTRA: cliente desconectou")
            if self.pending:
                self.cancel()
            elif self.applying:
                self.epoch += 1
                self.restore_requested = ""
            elif self.active:
                self.job("restore")
        elif name in ("org.freedesktop.UPower", "net.hadess.PowerProfiles") and new:
            if self.active:
                try:
                    self.hardware.profile("power-saver")
                except dbus.DBusException:
                    pass
            elif not self.applying:
                self.normal()

    def sleep_changed(self, sleeping: bool) -> None:
        logging.info("ULTRA: suspensao=%s", bool(sleeping))
        self.sleeping = bool(sleeping)
        if sleeping and self.pending:
            self.cancel()
        elif not sleeping:
            if self.active:
                if not self.hardware.battery():
                    self.job("restore", target="performance")
                elif not self.applying:
                    self.job("preset", preset=self.preset)
            else:
                self.normal()

    def session_check(self) -> bool:
        if self.pending and not self.hardware.local_session():
            self.cancel()
        return True


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Exige root.")
    DBusGMainLoop(set_as_default=True)
    config = parse_config(json.loads(Path("/etc/ultra-power.json").read_text()))
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        Hardware(config).restore()
        return
    bus = dbus.SystemBus()
    threads_init()
    service = Service(bus, config)
    loop = GLib.MainLoop()

    def quit_signal() -> bool:
        service.cancel()
        loop.quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, quit_signal)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, quit_signal)
    loop.run()


if __name__ == "__main__":
    main()
