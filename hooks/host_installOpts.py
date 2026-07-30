# installOpts hook for ReactOS. Host-side python, exec()'d into build.py's
# own globals, so build.py functions are bare names.
#
# Nothing is typed at the installer here. ReactOS setup is driven entirely
# by \reactos\unattend.inf on the remastered ISO (written by
# hooks/host_beforeBuild.sh), so this hook exists to do the two things the
# answer file cannot:
#
#   1. Wait for first stage to finish -- bounded, and FATAL on timeout.
#      The end-of-install signal is QEMU exiting: unattended usetup ends
#      first stage by rebooting unconditionally, and the conf's
#      VM_QEMU_NO_REBOOT turns that reboot into a clean QEMU exit. Without
#      it the reboot would land back on the install CD (the engine boots
#      cdrom-first) and unattended setup would reformat and reinstall in an
#      endless loop.
#
#   2. Copy the remote channel onto the installed volume while it is offline
#      (files/inject.sh). The payload cannot ride on the ISO because every
#      boot after the install runs with no media attached. Registering it as
#      a service happens in-guest instead, from unattend.inf's [GuiRunOnce]
#      -- doing it offline is impossible, see the header of inject.sh for
#      the hivex/ReactOS incompatibility that rules it out.
#
# main() calls _wait_vm_down(what="install") right after this hook returns.
# By then the VM is already down, so that call is a no-op -- the real,
# fatal bound lives here.

_ros_max = int(env("VM_INSTALL_MAX_SECONDS") or 2700)

log("reactos installOpts: waiting for unattended first stage to finish "
    "(bound %ds; QEMU exits at the installer's reboot via -no-reboot)"
    % _ros_max)

_ros_t0 = time.time()
while isRunning() == 0:
    time.sleep(20)
    _ros_elapsed = int(time.time() - _ros_t0)
    _ros_size, _ros_tail = _serial_tail_line()
    _ros_mm, _ros_ss = divmod(_ros_elapsed, 60)
    log("[%dm%02ds] reactos first stage, serial=%dB | %s"
        % (_ros_mm, _ros_ss, _ros_size, _ros_tail[:140]))
    if _ros_elapsed > _ros_max:
        # A bound that is only logged would let the rest of the pipeline
        # drive a dead guest for hours. Kill it and fail here, where the
        # log still shows the last console line.
        #
        # Photograph the screen BEFORE killing the VM. usetup's failure mode
        # is a modal error box that unattended setup does not dismiss, and
        # the box text is the entire diagnosis -- it says which step failed,
        # while the serial log only shows the last thing that succeeded. The
        # first time this fired ("Setup failed to add keyboard layouts to
        # the registry") the screen had to be recovered by reproducing the
        # whole 45-minute run by hand. Never again.
        _ros_shot = wf("install-timeout.ppm")
        try:
            qmon("screendump %s" % _ros_shot)
            time.sleep(2)
            log("reactos installOpts: screen captured to %s (%d bytes) -- "
                "open it, the error box on it is the diagnosis"
                % (_ros_shot, os.path.getsize(_ros_shot)))
        except Exception as _ros_e:
            log("reactos installOpts: could not capture the screen: %s"
                % _ros_e)
        log("FATAL: reactos first stage did not finish within %d s. Setup "
            "got far enough to keep the VM alive but stopped making "
            "progress -- read %s and the serial log tail above."
            % (_ros_max, _ros_shot))
        destroyVM()
        sys.exit(1)

log("reactos installOpts: first stage finished after %d s"
    % int(time.time() - _ros_t0))

must_sh("bash files/inject.sh", "reactos offline injection")

log("reactos installOpts: done")
