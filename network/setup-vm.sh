#!/usr/bin/env bash
# Run as your regular user on a fresh Ubuntu 22.04 VM: bash network/setup-vm.sh
set -Eeuo pipefail

if [[ "$EUID" -eq 0 ]]; then
    echo "Run without sudo; the script requests sudo when needed." >&2
    exit 1
fi
source /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 22.04 ]]; then
    echo "This script is intended for Ubuntu 22.04." >&2
    exit 1
fi

venv="$HOME/venvs/manta"
ryu_source="$HOME/src/ryu"
records="$HOME/manta-environment"
ryu_pid=""
test_started=false
shared_folder_setup=false
mkdir -p "$records"

cleanup() {
    if [[ -n "$ryu_pid" ]]; then
        kill "$ryu_pid" 2>/dev/null || true
        wait "$ryu_pid" 2>/dev/null || true
    fi
    if [[ "$test_started" == true ]]; then
        sudo mn -c > "$records/cleanup.log" 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'echo "Setup failed at line $LINENO. Check $records for test logs." >&2' ERR

echo "Installing Ubuntu packages..."
sudo -v
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
printf '%s\n' \
    'iperf3 iperf3/start_daemon boolean false' \
    'wireshark-common wireshark-common/install-setuid boolean false' \
    | sudo debconf-set-selections
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git curl build-essential python3-pip python3-venv python3-dev \
    mininet openvswitch-switch iperf3 nmap tcpdump wireshark
sudo systemctl enable --now openvswitch-switch

if [[ "$(systemd-detect-virt || true)" == oracle ]]; then
    echo "Setting up VirtualBox shared folders..."
    # Use Ubuntu's Guest Additions packages unless additions are already installed.
    if ! command -v VBoxControl >/dev/null 2>&1; then
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            virtualbox-guest-utils virtualbox-guest-x11
    fi
    sudo groupadd -f vboxsf
    sudo usermod -aG vboxsf "$(id -un)"
    shared_folder_setup=true
    cat <<'INFO'
In VirtualBox Settings > Shared Folders, add:
  Folder Path: E:\CodingProjects\MANTA
  Folder Name: MANTA
  Auto-mount: enabled; Make Permanent: enabled (if shown)
  Read-only: disabled; Mount point: leave empty
After reboot, access the project at /media/sf_MANTA.
Host files in this folder are not restored by VM snapshots; keep code in Git.
INFO
else
    echo "Not running in VirtualBox; skipping Guest Additions setup."
fi

echo "Installing Ryu in $venv..."
python3 -m venv "$venv"
"$venv/bin/python" -m pip install \
    'pip==23.2.1' 'setuptools==67.6.1' 'wheel==0.41.2' 'pbr==5.11.1'
mkdir -p "$HOME/src"
if [[ ! -e "$ryu_source" ]]; then
    git clone https://github.com/faucetsdn/ryu.git "$ryu_source"
fi
# Keep the existing checkout on reruns; record its commit below.
"$venv/bin/python" -m pip install --no-build-isolation \
    'eventlet==0.33.3' 'dnspython==2.3.0' "$ryu_source"
"$venv/bin/python" -m pip check
"$venv/bin/ryu-manager" --version

echo "Recording installed versions..."
"$venv/bin/python" -m pip freeze > "$records/python-packages.txt"
git -C "$ryu_source" rev-parse HEAD > "$records/ryu-commit.txt"
dpkg-query -W > "$records/ubuntu-packages.txt"
cp /etc/os-release "$records/os-release.txt"

echo "Checking Ryu and Mininet (run with no other lab network active)..."
# Fail before starting if another controller already owns the test port.
"$venv/bin/python" - <<'PY'
import socket
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 6653))
PY
"$venv/bin/ryu-manager" --ofp-listen-host 127.0.0.1 \
    --ofp-tcp-listen-port 6653 ryu.app.simple_switch_13 \
    > "$records/ryu.log" 2>&1 &
ryu_pid=$!

ready=false
for attempt in {1..30}; do
    if ! kill -0 "$ryu_pid" 2>/dev/null; then
        cat "$records/ryu.log" >&2
        exit 1
    fi
    if "$venv/bin/python" - <<'PY'
import socket
import sys
try:
    with socket.create_connection(("127.0.0.1", 6653), timeout=1):
        pass
except OSError:
    sys.exit(1)
PY
    then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    echo "Ryu did not start. See $records/ryu.log" >&2
    exit 1
fi

test_started=true
sudo timeout 120 mn --topo single,3 --mac \
    --switch ovs,protocols=OpenFlow13 \
    --controller remote,ip=127.0.0.1,port=6653 --test pingall \
    2>&1 | tee "$records/mininet-test.log"
if ! grep -Fq 'Results: 0% dropped' "$records/mininet-test.log"; then
    echo "Connectivity check failed. See $records/mininet-test.log" >&2
    exit 1
fi

cleanup
ryu_pid=""
test_started=false
echo "Setup and basic forwarding check passed. Records: $records"
if [[ -f /var/run/reboot-required || "$shared_folder_setup" == true ]]; then
    echo "Reboot before snapshotting to activate updates and shared-folder access."
    echo "After reboot, check: ls /media/sf_MANTA (if configured in VirtualBox)."
fi
echo "When ready: shut down the VM and take snapshot 02-mininet-ryu-working."
