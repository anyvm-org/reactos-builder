# enablessh hook for ReactOS (VM_TRANSPORT=telnet). Host-side python,
# exec()'d into build.py's globals.
#
# There is no sshd to enable, and unlike plan9-builder there is nothing to
# start either: unattend.inf's [GuiRunOnce] ran anyvminst.cmd at the end of
# second stage, which `sc create`s and starts the service, so the channel is
# already up before this hook runs (hooks/host_waitForLoginTag.py is what
# waited for it). main() nonetheless REQUIRES an enablessh hook for a
# telnet-transport guest -- it aborts when none ran -- and the useful work
# for this one is proving that what came up will still be there next boot.
#
# This is the ONLY gate on that, which makes it load-bearing rather than
# decorative: registering the service offline is impossible (see the header
# of files/inject.sh for the hivex/ReactOS hive incompatibility), so
# [GuiRunOnce] is the single path, and anyvminst.cmd's last-resort `start ""
# C:\anyvm\anyvmtd.exe` would make a build where `sc create` failed look
# perfectly healthy while shipping an image that is unreachable the moment
# anyvm.py boots it. So: REGISTERED and RUNNING, not merely listening.
#
# Marker convention: cmd.exe echoes every command it reads from the pipe, so
# a bare `echo foo-ok` would match its own echo. The caret is cmd's escape
# character -- `echo foo^-ok` prints "foo-ok" while the echoed command line
# still shows the caret. Same idea as the rc quote split plan9-builder uses.

log("reactos enablessh: verifying the anyvmtd service survives a reboot")

_ros_ok, _ros_text = telnet_exec([
    "sc query anyvmtd",
    "mkdir C:\\anyvm\\work",
    "if exist C:\\anyvm\\work echo workdir^-ok",
    "if exist C:\\anyvm\\anyvmtd.exe echo payload^-ok",
    "type C:\\anyvm\\anyvmtd.log",
], settle=5.0)

log("reactos enablessh transcript:\n%s" % _ros_text)

if not _ros_ok:
    log("FATAL: reactos enablessh: the telnet session dropped mid-check")
    sys.exit(1)

for _ros_marker in ("workdir-ok", "payload-ok"):
    if _ros_marker not in _ros_text:
        log("FATAL: reactos enablessh: marker %r missing -- the guest is "
            "answering but the anyvm payload is not where it should be"
            % _ros_marker)
        sys.exit(1)

# `sc query` prints the state as e.g. "STATE : 4  RUNNING". Requiring
# RUNNING here (not just that the service exists) is what separates a real
# boot-time service from anyvminst.cmd's one-shot fallback launch.
if "RUNNING" not in _ros_text.upper():
    log("FATAL: reactos enablessh: `sc query anyvmtd` does not report "
        "RUNNING. Something is listening on port 23, but it is not a "
        "registered service, so the exported image would come up "
        "unreachable under anyvm.py.")
    sys.exit(1)

log("reactos enablessh: anyvmtd is a running service; channel is durable")
