

```
blkid | grep -E 'modem_a|efs|modem_userdata'
```

Then mount read-only and copy:

```
mount -o ro /dev/sda12 /mnt/modem && cp /mnt/modem/images/default/modem.bin .
mount -o ro /dev/sda5  /mnt/efs   && cp /mnt/efs/nv_*.bin /mnt/nv/
mount -o ro /dev/sda7  /mnt/mud   && cp /mnt/mud/replay_region.bin /mnt/nv/
```

`images/default` is a symlink to the versioned build directory. The node
numbers are not fixed; on a Pixel 6 they came out as above.

## Building

```
make
```

On Alpine the Makefile adds `--rtlib=compiler-rt` when the compiler is clang,
since there is no libgcc.
