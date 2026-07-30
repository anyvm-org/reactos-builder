@echo off
rem In-guest registration for the anyvm telnet channel.
rem
rem Called once from unattend.inf's [GuiRunOnce] at the end of second
rem stage. This is the ONLY path that registers the service: writing the
rem key offline from the host is impossible, because hivex cannot add a
rem node to a ReactOS-written hive (its nk records carry no security
rem descriptor for a child to inherit -- full detail in files/inject.sh).
rem hooks/host_enablessh.py fails the build unless `sc query anyvmtd`
rem reports RUNNING, so a silent failure here cannot ship.
rem
rem Only tools that ship in the default install are used: ipconfig and sc
rem (base/applications/network/ipconfig, base/applications/sc). reg.exe is
rem deliberately not relied on.

set LOG=C:\anyvm\anyvminst.log
echo === anyvminst %DATE% %TIME% === >> %LOG%

rem --- network diagnostics (NOT a fix -- see below) ----------------------
rem On the very first boot after the install the DHCP request goes out while
rem second stage is still churning, times out, and ReactOS gives up:
rem   DHCPCSVC: Failed to receive a response from a DHCP server.
rem             An automatic private address will be assigned.
rem QEMU's slirp hostfwd points at a fixed 192.168.122.254, so the guest is
rem unreachable for the rest of that boot even though anyvmtd is listening
rem perfectly well -- from the host that looks like a service in STATE 4
rem RUNNING that nothing can ever connect to.
rem
rem `ipconfig /renew` was tried here as the fix and it DOES NOT WORK: the
rem log from a real build shows the adapter still at
rem   IP Address . . . : 0.0.0.0
rem   Subnet Mask  . . : 0.0.0.0
rem straight after the renew. What actually fixes it is a reboot, which
rem hooks/host_waitForLoginTag.py performs from the host once second stage
rem has settled. The two calls stay because their output in this log is how
rem that diagnosis was made and how the next one will be -- they are
rem evidence, not a remedy. Do not delete them believing they fix anything,
rem and do not delete the reboot believing these do.
echo --- ipconfig /renew (diagnostic; known not to recover the lease) --- >> %LOG%
ipconfig /renew >> %LOG% 2>&1
echo --- ipconfig /all --- >> %LOG%
ipconfig /all >> %LOG% 2>&1

rem --- then register and start the channel. ------------------------------
sc create anyvmtd binPath= C:\anyvm\anyvmtd.exe start= auto DisplayName= "anyvm telnet daemon" >> %LOG% 2>&1
sc start anyvmtd >> %LOG% 2>&1

rem Last resort, and ONLY if the SCM refused: run it directly. This is
rem gated rather than unconditional because an unconditional launch leaves
rem a stray console window sitting on the desktop of the shipped image.
rem (It does not break the port -- two instances binding 23 was tested and
rem the channel still answered -- it is purely cosmetic.)
if errorlevel 1 (
    echo sc start failed, launching standalone >> %LOG%
    start "" C:\anyvm\anyvmtd.exe
)

sc query anyvmtd >> %LOG% 2>&1
exit /b 0
