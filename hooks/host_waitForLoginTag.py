# waitForLoginTag hook for ReactOS. Host-side python, exec()'d into
# build.py's globals.
#
# ReactOS puts only kernel debug output on COM1, never a shell, so there is
# no login prompt for the engine's waitForText() to find and no VM_LOGIN_TAG
# worth setting. A waitForLoginTag hook short-circuits start_and_wait()
# entirely, which also means it disables the engine's own
# VM_LOGIN_MAX_SECONDS bound and its boot reroll -- so every bound below is
# ours to carry, and every one of them is fatal.
#
# ---------------------------------------------------------------------------
# Why this is three phases and not just "wait for telnet"
# ---------------------------------------------------------------------------
# The first boot after the install OFTEN comes up with no networking at all
# -- intermittently, not always: three of the four post-install boots
# measured while building this. The DHCP request goes out while second stage
# is still churning, times out, and ReactOS gives up for good --
#
#   DHCPCSVC: Failed to receive a response from a DHCP server.
#             An automatic private address will be assigned.
#
# -- after which `ipconfig /all` in the guest reports a flat 0.0.0.0 with no
# gateway (not even an APIPA address), and `ipconfig /renew`, which
# anyvminst.cmd runs, does NOT recover it. Meanwhile anyvmtd is up and
# happily listening: `sc query` says STATE 4 RUNNING. The service is simply
# unreachable, because QEMU's slirp hostfwd points at a fixed
# 192.168.122.254 and the guest is not on that address. A second, quiet boot
# leases normally, every time.
#
# When the race goes the other way the guest leases on the first boot
# (verified: `Lease Obtained ... 192.168.122.254` in the in-guest log, and no
# DHCPCSVC failure on the serial). Phase 1 below then breaks early and the
# whole build finishes in about eight minutes instead of the twenty-odd a
# power cycle costs -- so do not "simplify" this into an unconditional
# reboot.
#
# What must NOT happen is rebooting while second stage is still running:
# that is when [GuiRunOnce] -> anyvminst.cmd -> `sc create` registers the
# service, and cutting power through it would lose the registration. Hence
# the settle wait before the power cycle, rather than a fixed sleep.
#
# Hence: wait for the guest to go QUIET on the serial log (a generic,
# implementation-independent "it has finished booting and settled" signal --
# ReactOS's kernel debug output is continuous through second stage and stops
# at idle), THEN power cycle, THEN wait for the channel. Phase 1 also breaks
# early if telnet answers on its own, so a future ReactOS that gets its lease
# on the first boot costs nothing.
#
# Before this, phase 1 was just "run the full telnet budget, then reboot",
# which worked but burned 26 idle minutes on every single build.

_ros_tries = int(env("VM_TELNET_MAX_RETRIES") or 100)
_ros_idle = int(env("VM_SERIAL_IDLE_SECONDS") or 90)
_ros_settle_max = int(env("VM_SECOND_STAGE_MAX_SECONDS") or 2400)

# ---------------------------------------------------------------------------
# Phase 1 -- let second stage finish
# ---------------------------------------------------------------------------
log("reactos waitForLoginTag: phase 1, waiting for second stage to settle "
    "(serial quiet for %ds, cap %ds)" % (_ros_idle, _ros_settle_max))

_ros_t0 = time.time()
_ros_last_size = -1
_ros_last_change = time.time()
_ros_reachable = False

while True:
    time.sleep(10)
    _ros_elapsed = int(time.time() - _ros_t0)

    if _telnet_ready_check():
        log("reactos waitForLoginTag: guest answered during phase 1 after "
            "%ds -- no power cycle needed" % _ros_elapsed)
        _ros_reachable = True
        break

    _ros_size, _ros_tail = _serial_tail_line()
    if _ros_size != _ros_last_size:
        _ros_last_size = _ros_size
        _ros_last_change = time.time()
    _ros_quiet = int(time.time() - _ros_last_change)

    _ros_mm, _ros_ss = divmod(_ros_elapsed, 60)
    log("[%dm%02ds] reactos second stage, serial=%dB quiet=%ds | %s"
        % (_ros_mm, _ros_ss, _ros_size, _ros_quiet, _ros_tail[:110]))

    if _ros_quiet >= _ros_idle:
        log("reactos waitForLoginTag: serial has been quiet for %ds; second "
            "stage is done" % _ros_quiet)
        break

    if _ros_elapsed > _ros_settle_max:
        _ros_shot = wf("secondstage-timeout.ppm")
        try:
            qmon("screendump %s" % _ros_shot)
            time.sleep(2)
            log("reactos: screen captured to %s (%d bytes)"
                % (_ros_shot, os.path.getsize(_ros_shot)))
        except Exception as _ros_e:
            log("reactos: could not capture the screen: %s" % _ros_e)
        log("FATAL: reactos second stage neither settled nor answered within "
            "%d s. Second stage is graphical, so %s shows what it is stuck "
            "on -- a modal dialog is the usual answer."
            % (_ros_settle_max, _ros_shot))
        destroyVM()
        sys.exit(1)

# ---------------------------------------------------------------------------
# Phase 2 -- power cycle so the guest gets a real DHCP lease
# ---------------------------------------------------------------------------
if not _ros_reachable:
    log("reactos waitForLoginTag: phase 2, power cycling for a clean DHCP "
        "lease")
    # ACPI powerdown, not a straight kill: `sc create` wrote the anyvmtd
    # service key minutes ago and a hard reset risks losing an unflushed
    # hive. _wait_vm_down force-kills at its own bound if the guest ignores
    # the power button.
    if isRunning() == 0:
        shutdownVM()
    _wait_vm_down(what="reactos power cycle", poll=5, max_seconds=300)
    closeConsole()
    if startVM() != 0:
        log("FATAL: reactos: could not restart the VM for the second boot")
        sys.exit(1)
    time.sleep(2)
    openConsole()

# ---------------------------------------------------------------------------
# Phase 3 -- the channel must come up
# ---------------------------------------------------------------------------
    log("reactos waitForLoginTag: phase 3, waiting for anyvmtd on port 23 "
        "(up to %d probes, ~10 s apart)" % _ros_tries)

    if not _wait_telnet(max_retries=_ros_tries):
        _ros_shot = wf("boot-timeout.ppm")
        try:
            qmon("screendump %s" % _ros_shot)
            time.sleep(2)
            log("reactos waitForLoginTag: screen captured to %s (%d bytes)"
                % (_ros_shot, os.path.getsize(_ros_shot)))
        except Exception as _ros_e:
            log("reactos waitForLoginTag: could not capture the screen: %s"
                % _ros_e)
        log("FATAL: reactos never answered on the telnet channel, even after "
            "a clean second boot. Either [GuiRunOnce] did not run "
            "anyvminst.cmd, or `sc create` failed, or the guest still has no "
            "DHCP lease -- C:\\anyvm\\anyvminst.log in the image records all "
            "three, and %s shows the desktop." % _ros_shot)
        # Leaving QEMU running would keep the qcow2 write-locked and make the
        # image impossible to inspect afterwards.
        destroyVM()
        sys.exit(1)

log("reactos waitForLoginTag: guest is reachable")
