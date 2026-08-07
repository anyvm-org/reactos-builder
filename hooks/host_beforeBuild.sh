#!/bin/bash
# beforeBuild hook -- build the install media this builder actually boots.
#
# Runs before setup(), which is the point of this hook point: it exists so a
# builder can generate build inputs on the fly (ubuntu-builder compiles its
# pinned QEMU here) instead of committing binaries to git.
#
# Three products, all under $VM_WORKDIR:
#
#   anyvmtd.exe    the Win32 telnet server from files/anyvmtd.c, cross
#                  compiled for i686. ReactOS has no remote-access server
#                  of its own, so the builder ships one. It is NOT put on
#                  the ISO -- hooks/host_installOpts.py injects it straight
#                  into the installed FAT volume after first stage, because
#                  the CD is detached for every boot after the install.
#
#   anyvminst.cmd  the in-guest registration script GuiRunOnce calls.
#
#   reactos.iso    the upstream bootcd remastered with \reactos\unattend.inf
#                  so setup runs unattended. This is written to exactly the
#                  path createVM() would download to, and createVM() skips
#                  its own download when the file already exists -- which is
#                  why VM_ISO_LINK (a .zip) is never fetched by the engine.
#
# Any failure here is fatal by design: run_hook() aborts the build on a
# non-zero host .sh hook, and continuing would boot the guest off whatever
# happened to be at build/reactos.iso.

set -euo pipefail

WORK="${VM_WORKDIR:-build}"
FILES="$(pwd)/files"
mkdir -p "$WORK"

ZIP="$WORK/reactos-upstream.zip"
UNPACK="$WORK/upstream"
OUT_ISO="$WORK/${VM_OS_NAME}.iso"

echo "=== reactos beforeBuild: host deps ==="
export DEBIAN_FRONTEND=noninteractive
sudo -E apt-get update -q
sudo -E apt-get install -y -q --no-install-recommends \
    unzip xorriso curl gcc-mingw-w64-i686

echo "=== reactos beforeBuild: cross-compiling anyvmtd.exe ==="
# -static so the guest needs no runtime DLLs beyond what ReactOS itself
# provides; -Wall -Wextra kept on because this binary is the whole remote
# channel and a silent miscompile would look like a boot failure.
i686-w64-mingw32-gcc -O2 -Wall -Wextra -static \
    -o "$WORK/anyvmtd.exe" "$FILES/anyvmtd.c" -lws2_32
cp "$FILES/anyvminst.cmd" "$WORK/anyvminst.cmd"
ls -l "$WORK/anyvmtd.exe"

echo "=== reactos beforeBuild: fetching busybox-w32 (guest tar for --sync tar) ==="
# ReactOS ships no archiver at all (no tar/zip; extrac32 is extract-only
# CAB and certutil is a -hashfile stub), so the tar sync method needs a
# guest-side tar and busybox-w32 provides one: a single static PE32,
# GPL-licensed like ReactOS itself, verified to run there (uname reports
# Windows_NT reactos 5.2 3790 i686). Pinned build + sha256 so the image
# is reproducible; frippery.org rejects botlike user agents, hence -A.
BUSYBOX_VER="FRP-6075-g169694ebd"
BUSYBOX_SHA256="7bfee530965315665044e6e01db58125f2763c8a39c2e72ba1a6beb6923e0e1f"
BUSYBOX_URL="https://frippery.org/files/busybox/busybox-w32-${BUSYBOX_VER}.exe"
if [ ! -f "$WORK/busybox.exe" ] \
   || ! echo "$BUSYBOX_SHA256  $WORK/busybox.exe" | sha256sum -c --quiet -; then
    curl -fSL --retry 5 --retry-delay 5 \
         -A "Mozilla/5.0 (X11; Linux x86_64)" \
         -o "$WORK/busybox.exe.part" "$BUSYBOX_URL"
    echo "$BUSYBOX_SHA256  $WORK/busybox.exe.part" | sha256sum -c -
    mv "$WORK/busybox.exe.part" "$WORK/busybox.exe"
fi
ls -l "$WORK/busybox.exe"

echo "=== reactos beforeBuild: fetching upstream bootcd ==="
if [ ! -f "$ZIP" ]; then
    curl -fSL --retry 5 --retry-delay 5 -o "$ZIP.part" "$VM_ISO_LINK"
    mv "$ZIP.part" "$ZIP"
fi
ls -l "$ZIP"

rm -rf "$UNPACK"
mkdir -p "$UNPACK"
unzip -q -o "$ZIP" -d "$UNPACK"

# The release zip holds exactly one .iso (the bootcd); refuse to guess if
# that ever stops being true rather than picking an arbitrary one.
mapfile -t ISOS < <(find "$UNPACK" -maxdepth 2 -type f -name '*.iso' | sort)
if [ "${#ISOS[@]}" -ne 1 ]; then
    echo "FATAL: expected exactly one .iso in $ZIP, found ${#ISOS[@]}:" >&2
    printf '  %s\n' "${ISOS[@]}" >&2
    exit 1
fi
SRC_ISO="${ISOS[0]}"
echo "upstream bootcd: $SRC_ISO"

echo "=== reactos beforeBuild: remastering with unattend.inf ==="
# "-boot_image any replay" carries the El Torito boot record from the input
# image over to the output unchanged. Extracting and re-authoring the tree
# instead would mean reconstructing ReactOS's boot catalog by hand, which
# is exactly the kind of thing that produces an ISO that looks fine and
# does not boot.
rm -f "$OUT_ISO"
xorriso -indev "$SRC_ISO" -outdev "$OUT_ISO" \
        -boot_image any replay \
        -map "$FILES/unattend.inf" /reactos/unattend.inf \
        -commit

# Prove the file actually landed -- a silently dropped -map would give a
# perfectly bootable ISO that then sits on the interactive first page of
# setup until the login wait times out.
echo "--- verifying /reactos/unattend.inf on the remastered image ---"
xorriso -indev "$OUT_ISO" -lsl /reactos/unattend.inf
xorriso -osirrox on -indev "$OUT_ISO" \
        -extract /reactos/unattend.inf "$WORK/unattend.check.inf" >/dev/null
if ! cmp -s "$FILES/unattend.inf" "$WORK/unattend.check.inf"; then
    echo "FATAL: unattend.inf on the remastered ISO does not match the source" >&2
    exit 1
fi
rm -f "$WORK/unattend.check.inf"

ls -lh "$OUT_ISO"
echo "=== reactos beforeBuild: done ==="
