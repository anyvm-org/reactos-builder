How the images are built:

Each image is built automatically in the
[anyvm-org/reactos-builder](https://github.com/anyvm-org/reactos-builder)
repo's GitHub Actions: it downloads the official ReactOS release ISO,
boots it in QEMU, runs the ReactOS setup unattended, adds a telnet
service for remote access, and exports the installed disk as a
compressed qcow2 image.

Upstream install media: the official ReactOS release ISOs from
https://github.com/reactos/reactos/releases (download page:
https://reactos.org/download/).
