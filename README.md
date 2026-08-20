

[![Build](https://github.com/anyvm-org/reactos-builder/actions/workflows/build.yml/badge.svg)](https://github.com/anyvm-org/reactos-builder/actions/workflows/build.yml)

Latest: v2.0.2


The image builder for `reactos`


All the supported releases are here:



| Release | i386 (x86 32-bit) |
|---------|---------|
| 0.4.15 | ✅ (tar) |

<!-- arch-label: i386 = i386 (x86 32-bit) -->

> **Note:** ReactOS support is a **tech preview**. Remote command execution
> works, and file sync works via `tar` streamed over the same telnet channel:
> ReactOS ships no archiver of its own, so the builder bakes **busybox-w32**
> (a single static GPL PE32, verified to run on ReactOS) at `C:\anyvm\tar.exe`
> and the host streams a ustar archive down the connection in both
> directions (`anyvmtd` escapes outbound IAC so the binary stream survives).
>
> ReactOS ships no remote-access server of any kind -- `base/applications/network`
> has a telnet *client*, and the rapps database offers only PuTTY and WinSCP,
> also clients -- so this builder supplies its own: `files/anyvmtd.c`, a small
> Win32 telnet server cross-compiled with mingw-w64, copied onto the installed
> volume while it is offline and registered as a boot-time service from the
> unattended answer file. The guest is driven over that channel
> (`VM_TRANSPORT=telnet`, the same engine path plan9-builder uses).
> It authenticates nobody and is reachable only through the loopback-bound
> QEMU hostfwd.
>
> For sync, the guest was surveyed live rather than assumed. There is no sshd
> and no ssh client (so no rsync / sshfs / scp), no 9P client, no SMB
> redirector at all (`mrxsmb.sys` is absent, which also rules out QEMU's
> built-in slirp `smb=` share), and ReactOS's `certutil` is a stub with only
> `-hashfile` -- no `-encode` / `-decode`, so even a base64-over-the-telnet-
> channel fallback is out.
>
> **NFS is the interesting one: it ships, it is correctly registered, and it
> still does not work.** The image carries the full ms-nfs41-client stack
> (`nfs41_driver.sys`, `nfsd.exe`, `nfs41_np.dll`), `ProviderOrder` is already
> `nfs41_driver`, and `net start nfs41_driver` reaches STATE 4 RUNNING. But
> the daemon service -- named `pnfs`, not `nfsd` -- hangs in START_PENDING,
> and `net use Z: \\<host>\<export>` fails with System error 2 against an
> export the guest can ping. That is a ReactOS bug, not a builder gap.
>
> (The other candidates that would have needed new code -- driving the guest's
> `ftp`/`ncftp` client from a host-side FTP server, or a put/get protocol in
> `anyvmtd` -- are superseded by the tar stream.)

> **Note:** 0.4.15 (2025-03-21) is the newest ReactOS *release*, and x86
> 32-bit is the only architecture it ships. Newer-looking tags in the
> repository -- `0.4.16`, `0.4.16-RC2`, `0.4.17-dev` -- are bare git tags with
> no published release and no downloadable media (`releases/tags/0.4.16`
> answers 404), which is why `hooks/upstream_check.py` filters on a real
> release carrying an `*-iso.zip` asset rather than on tag names.




How to build:

1. Use the [manual.yml](.github/workflows/manual.yml) to build manually.
   
    Run the workflow manually, you will get a view-only webconsole from the output of the workflow, just open the link in your web browser.
   
    You will also get an interactive VNC connection port from the output, you can connect to the vm by any vnc client.

2. Run the builder locally on your Ubuntu machine.

    Just clone the repo. and run:
    ```bash
    python3 build.py conf/reactos-0.4.15-i386.conf
    ```
   
