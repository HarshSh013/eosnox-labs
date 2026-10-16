"""This computer's network adapters, and the address the H33 sends results to.

The H33 connects out to a fixed "server" address (192.168.19.104 on its
Setup screen). The computer must hold that address on the adapter the H33
is cabled to. Adding it needs administrator rights.

macOS: added as an extra address with ifconfig; macOS asks for the Mac
password and it lasts until the adapter is unplugged or the Mac restarts.
Windows: the adapter is given that fixed address with netsh (Windows asks
for permission) and keeps it across restarts. A second, self-assigned
169.254.x address is added too, so a 100plusV on the same cable stays
reachable.
"""

import ipaddress
import re
import subprocess
import sys

WINDOWS = sys.platform == "win32"
# Adapters an analyzer is never on.
_NOT_CABLED = ("Wi-Fi", "Wireless", "WLAN", "Bluetooth", "Thunderbolt Bridge", "Loopback", "vEthernet", "VirtualBox",
               "VMware", "Hyper-V")


def is_cabled(port):
    return not any(word.lower() in port.lower() for word in _NOT_CABLED)


def adapters():
    if WINDOWS:
        return _adapters_windows()
    return _adapters_mac()


def _adapters_windows():
    import psutil

    out = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        if "loopback" in name.lower():
            continue
        st = stats.get(name)
        ipv4, mac = [], ""
        for a in addrs:
            if a.family == psutil.AF_LINK:
                mac = a.address.replace("-", ":").lower()
            elif getattr(a.family, "name", "") == "AF_INET" or a.family == 2:
                prefix = bin(int(ipaddress.IPv4Address(a.netmask or "255.255.255.255"))).count("1")
                ipv4.append((a.address, prefix))
        out.append({
            "port": name,
            "device": name,
            "mac": mac,
            "active": bool(st and st.isup),
            "media": "{} Mbit/s".format(st.speed) if st and st.speed else "",
            "ipv4": ipv4,
        })
    out.sort(key=lambda a: (not a["active"], not is_cabled(a["port"]), a["port"]))
    return out


def _adapters_mac():
    """[{port, device, mac, active, media, ipv4: [(ip, prefix)]}] for every adapter macOS lists."""
    ports = {}
    listing = subprocess.run(["networksetup", "-listallhardwareports"], capture_output=True, text=True).stdout
    for port, device in re.findall(r"Hardware Port: (.+)\nDevice: (\S+)", listing):
        ports[device] = port
    out = []
    text = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
    for block in re.split(r"\n(?=\S)", text):
        name = block.split(":", 1)[0]
        if name not in ports:
            continue
        ipv4 = []
        for ip, mask in re.findall(r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]+)", block):
            ipv4.append((ip, bin(int(mask, 16)).count("1")))
        mac = re.search(r"ether ([0-9a-f:]+)", block)
        media = re.search(r"media: (.+)", block)
        status = re.search(r"status: (\w+)", block)
        out.append({
            "port": ports[name],
            "device": name,
            "mac": mac.group(1) if mac else "",
            "active": bool(status and status.group(1) == "active"),
            "media": media.group(1).strip() if media else "",
            "ipv4": ipv4,
        })
    # Connected cabled adapters first: an analyzer is never on Wi-Fi.
    out.sort(key=lambda a: (not a["active"], not is_cabled(a["port"]), a["port"]))
    return out


def holder_of(ip):
    """Device holding ip on this Mac, or ''."""
    for adapter in adapters():
        if any(addr == ip for addr, _ in adapter["ipv4"]):
            return adapter["device"]
    return ""


def _checked(device, ip, prefix):
    if WINDOWS:
        if device not in {a["device"] for a in adapters()}:
            raise ValueError("Unknown adapter.")
    elif not re.fullmatch(r"[a-z]+[0-9]+", device or ""):
        raise ValueError("Unknown adapter.")
    address = ipaddress.IPv4Address(ip)
    if not 8 <= int(prefix) <= 30:
        raise ValueError("Network prefix must be between 8 and 30.")
    mask = str(ipaddress.IPv4Network("0.0.0.0/{}".format(prefix)).netmask)
    return device, str(address), mask


def _as_admin(command):
    script = 'do shell script "{}" with administrator privileges'.format(command)
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if done.returncode != 0:
        message = done.stderr.strip() or "macOS did not allow the change."
        if "User canceled" in message or "-128" in message:
            message = "Cancelled."
        raise RuntimeError(message)


def _as_admin_windows(netsh_lines):
    """Run netsh commands with administrator rights; Windows shows its permission prompt once."""
    import base64

    quote = lambda arg: "'{}'".format(arg.replace("'", "''"))
    inner = "\n".join(
        "& netsh {} | Out-Null; if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}".format(" ".join(quote(a) for a in args))
        for args in netsh_lines
    )
    encoded = base64.b64encode(inner.encode("utf-16-le")).decode()
    outer = ("$p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ErrorAction Stop "
             "-ArgumentList '-NoProfile','-EncodedCommand','{}'; exit $p.ExitCode").format(encoded)
    done = subprocess.run(["powershell", "-NoProfile", "-Command", outer], capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode != 0:
        message = done.stderr.strip()
        if "cancel" in message.lower():
            raise RuntimeError("Cancelled.")
        raise RuntimeError("Windows did not allow the change (netsh exit code {}).".format(done.returncode))


def add_address(device, ip, prefix=24):
    device, ip, mask = _checked(device, ip, prefix)
    if WINDOWS:
        _as_admin_windows([
            ["interface", "ipv4", "set", "address", "name={}".format(device), "static", ip, mask],
            # Keep a self-assigned address too, so a 100plusV on this cable is still reachable.
            ["interface", "ipv4", "add", "address", "name={}".format(device), "169.254.19.104", "255.255.0.0"],
        ])
        return
    _as_admin("/sbin/ifconfig {} alias {} {}".format(device, ip, mask))


def remove_address(device, ip):
    device, ip, _ = _checked(device, ip, 24)
    if WINDOWS:
        # Back to automatic (DHCP / self-assigned), as Windows had it.
        _as_admin_windows([["interface", "ipv4", "set", "address", "name={}".format(device), "dhcp"]])
        return
    _as_admin("/sbin/ifconfig {} -alias {}".format(device, ip))


def reachable(host, timeout=2):
    if WINDOWS:
        done = subprocess.run(["ping", "-n", "1", "-w", str(timeout * 1000), host], capture_output=True,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        done = subprocess.run(["ping", "-c", "1", "-t", str(timeout), host], capture_output=True)
    return done.returncode == 0
