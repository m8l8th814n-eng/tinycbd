# tinycbd

A small userspace CP boot daemon for the Shannon s5123 modem in the Google
Pixel 6 (gs101), for use with the mainline kernel and the ported `cpif`
driver. It replaces the parts of Android's `cbd` that are needed to get the
modem out of reset.

It talks to `cpif` through `/dev/umts_boot0` and drives the boot ioctls
directly:

```
IOCTL_POWER_ON            IOCTL_POWER_OFF
IOCTL_LOAD_CP_IMAGE       IOCTL_START_CP_BOOTLOADER
IOCTL_COMPLETE_NORMAL_BOOTUP
IOCTL_GET_CP_STATUS
```

## Usage

```
tinycbd [-d node] [-i modem.bin] [-n nvdir] <command>

  toc            list the sections in the image
  boot           upload BOOT and start the CP bootloader
  upload <NAME>  upload one section by name
  status         query CP status
  poweron        IOCTL_POWER_ON
  poweroff       IOCTL_POWER_OFF
```

`NV_NORM`, `NV_PROT` and `REPLAY` are not taken from the image. They come from
`nvdir`: `nv_normal.bin` and `nv_protected.bin` from the `efs` partition, and
`replay_region.bin` from `modem_userdata`.

Defaults are `-d /dev/umts_boot0`, `-i ./modem.bin`, `-n /mnt/nv`.

## Building

```
make
```

On Alpine the Makefile adds `--rtlib=compiler-rt` when the compiler is clang,
since there is no libgcc.
