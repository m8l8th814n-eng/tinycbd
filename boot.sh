mkdir ~/modemlog/  
sudo python3 ~/tinycbd/modemlisten.py &
sleep 2
  sudo ./tinycbd -i ./modem.bin -n ./nv full
sleep 10
  dmesg | grep -E "relink: PHY re-init|Link failed|link up|restore_state|D0|doorbell"

tail -f ~/modemlog/oem_ipc*.log


