# GNOME ULTRA Power

Battery mode confirmed by fingerprint or an explicit red OK button for light browsing and terminal work, integrated into GNOME's power menu. The battery icon turns yellow while active. UI strings are currently Portuguese.

**v1 supports Ubuntu 24.04, GNOME 46, Wayland, x86_64 only.** A laptop battery is required. An enrolled fprintd-compatible reader is optional: use the red OK button if unavailable. Other active power coordinators are rejected. CPU discovery is portable; physical testing on every Intel/AMD laptop is not claimed.

Download the complete tarball and checksums from the [release](https://github.com/vcanonici/gnome-ultra-power/releases/latest), verify the SHA256 checksum, extract it and run:

```bash
sudo /usr/bin/python3 scripts/install.py doctor --user "$USER"
sudo /usr/bin/python3 scripts/install.py install --user "$USER"
```

Missing runtime packages on the supported Ubuntu: `python3-dbus python3-gi fprintd libpam-fprintd power-profiles-daemon`. Optionally enroll with `fprintd-enroll` as your normal user. Save work and log out/in after installation.

Unplug AC, choose ULTRA and confirm with a fresh fingerprint or the red OK button. This confirmation helps avoid accidental activation; technical safeguards and recovery remain in the controller. Default policy restricts your session to up to two physical cores, disables turbo where supported, reduces available screen/keyboard lighting and pauses the user indexer/remote desktop. Wi-Fi, VPNs and browser tabs are preserved. CPU hotplug and Docker/libvirt interruption are explicit installer opt-ins, disabled by default.

GPU runtime suspend depends on hardware and current workloads; an active NVIDIA means **partial savings**, clearly reported. The compositor is never force-killed. The installer does not switch GPU drivers or modify graphical-login/sudo PAM.

Return to a normal power profile to leave ULTRA; AC also leaves ULTRA automatically. `ultra-power off` requests recovery. To uninstall:

```bash
sudo /usr/bin/python3 scripts/install.py remove
```

Uninstall restores pending adjustments and removes only ULTRA's files/extension. Log out/in to unload the UI. See the [Portuguese README](../README.md), [recovery guide](RECOVERY.md) and [validation scope](VALIDATION.md) for full details. No battery-life-hour claims are made.
