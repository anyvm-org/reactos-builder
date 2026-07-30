#!/bin/bash
# Offline injection into the freshly installed ReactOS volume.
#
# Called by hooks/host_installOpts.py once first stage has ended and QEMU
# has exited (VM_QEMU_NO_REBOOT turns the installer's own reboot into a
# clean exit), so the qcow2 is idle and safe to mount.
#
# What goes in: C:\anyvm\anyvmtd.exe + anyvminst.cmd. They cannot ride on
# the ISO, because every boot after the install is launched with no media
# attached -- the CD is simply not there when they are needed.
#
# WHAT DELIBERATELY DOES NOT HAPPEN HERE: registering the anyvmtd service by
# editing the SYSTEM hive offline. That was tried and it cannot work.
# hivex refuses to add any node to a ReactOS-written hive -- at the root
# just as much as deep in the tree -- and HIVEX_DEBUG=1 gives the exact
# reason:
#
#   hivex_node_add_child: returning EFAULT because:
#     parent sk is not a valid block (4294971391)
#
# 4294971391 is 0x100000FFF, i.e. hivex resolving a parent `sk` (security
# descriptor) offset of 0xFFFFFFFF -- unset. ReactOS's setup writes nk
# records with no security descriptor, and hivex makes a new child inherit
# the parent's sk, so it has nothing to inherit and bails. (The same hive
# READS fine: 73 subkeys enumerate under ControlSet001\Services.) This is a
# genuine ReactOS-vs-hivex format incompatibility, not a usage error, so do
# not re-add the hivexsh step.
#
# Service registration therefore happens in-guest instead, from
# unattend.inf's [GuiRunOnce] -> anyvminst.cmd -> `sc create`, which goes
# through the SCM and writes a well-formed key. hooks/host_enablessh.py is
# the gate: it fails the build unless `sc query anyvmtd` reports RUNNING,
# so a guest where that never happened cannot ship.

set -euo pipefail

WORK="${VM_WORKDIR:-build}"
QCOW="${VM_WORK_QCOW:-${VM_OS_NAME}.qcow2}"
NBD=/dev/nbd0
MNT="$(pwd)/$WORK/mnt-reactos"

echo "=== reactos inject: $QCOW ==="


_cleanup() {
    sudo umount "$MNT" 2>/dev/null || true
    sudo qemu-nbd --disconnect "$NBD" 2>/dev/null || true
}
trap _cleanup EXIT

mkdir -p "$MNT"
sudo modprobe nbd max_part=16
sudo qemu-nbd --disconnect "$NBD" 2>/dev/null || true
sudo qemu-nbd --connect="$NBD" "$QCOW"
sudo partprobe "$NBD" 2>/dev/null || true
sleep 2

if [ ! -b "${NBD}p1" ]; then
    echo "FATAL: ${NBD}p1 did not appear -- first stage did not partition the disk" >&2
    sudo fdisk -l "$NBD" >&2 || true
    exit 1
fi

sudo mount -t vfat "${NBD}p1" "$MNT"
echo "--- installed volume ---"
sudo ls -la "$MNT"

# The Linux vfat driver matches names case-insensitively, so the exact case
# ReactOS wrote does not matter here.
HIVE="$MNT/ReactOS/system32/config/SYSTEM"
if [ ! -f "$HIVE" ]; then
    echo "FATAL: no SYSTEM hive at $HIVE -- the install did not complete" >&2
    sudo find "$MNT" -maxdepth 3 -iname 'SYSTEM*' >&2 || true
    exit 1
fi

echo "--- copying the anyvm payload ---"
sudo mkdir -p "$MNT/anyvm"
sudo cp "$WORK/anyvmtd.exe"   "$MNT/anyvm/anyvmtd.exe"
sudo cp "$WORK/anyvminst.cmd" "$MNT/anyvm/anyvminst.cmd"
sudo ls -l "$MNT/anyvm"

echo "--- NOT registering the service offline (see the header) ---"
echo "anyvmtd is registered in-guest by unattend.inf [GuiRunOnce] ->"
echo "anyvminst.cmd -> sc create; hooks/host_enablessh.py is the gate."

# Targeted syncfs of just this mount -- NEVER a bare global `sync` (it
# wedges WSL drvfs; see the blissos notes).
sync -f "$MNT" 2>/dev/null || true
sudo umount "$MNT"
sudo qemu-nbd --disconnect "$NBD"

# `qemu-nbd --disconnect` returns once the kernel client is gone, but the
# server process spawned by --connect still holds an exclusive write lock
# on the image and exits asynchronously. The next build step needs that
# lock. Wait for both signals: the kernel binding is gone (/sys/block/<nbd>/
# pid exists only while connected) AND a no-op resize (which takes the exact
# same lock) succeeds. Same pattern as plan9-builder's prepareImage hook.
_nbd_name="$(basename "$NBD")"
for _try in $(seq 1 120); do
    if [ ! -e "/sys/block/${_nbd_name}/pid" ] \
       && qemu-img resize "$QCOW" +0 >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done
trap - EXIT

sudo chmod 0666 "$QCOW" 2>/dev/null || true
echo "=== reactos inject: done ==="
ls -lh "$QCOW"
