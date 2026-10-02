# eSIM on the Pixel 6 modem with lpac

`esim.py` runs [lpac](https://github.com/estkme-group/lpac) with its `stdio`
APDU driver and forwards every APDU to the eUICC through the modem's SIT
interface (`/dev/umts_ipc1`, `SLOT=0` for `umts_ipc0`).

Status: lpac reaches `logic_channel_open`; the SIT side is not yet confirmed
on hardware.

## lpac

lpac 2.3.0 (Alpine package) has a broken stdio driver: `connect`,
`logic_channel_open` and `transmit` test `json_request()` the wrong way round
and always fail with `euicc_init`. Build lpac from `main` instead:

```sh
sudo apk del lpac
sudo apk add cmake curl-dev
git clone https://github.com/estkme-group/lpac && cd lpac
cmake -B build -DBUILD_TESTING=OFF && cmake --build build
sudo cmake --install build        # /usr/local/bin/lpac
```

`esim.py` uses `/usr/local/bin/lpac` when it exists.

## Use

The modem must be ONLINE (`tinycbd full`). Stop anything else reading
`umts_ipc*` (`sitctl.py listen`, `modemlisten.py`), or it takes the replies.

```sh
sudo ./esim.py chip info
sudo ./esim.py profile list
sudo ./esim.py profile download -a 'LPA:1$smdp.example$MATCHING-ID'
sudo ./esim.py profile enable <iccid>
sudo ./esim.py profile delete <iccid>
sudo DEBUG=1 ./esim.py chip info  # trace on stderr
```

`esim_qr_to_code_convert.py image.png` reads the activation code from a QR
image (needs `zbarimg`, `apk add zbar`).

## lpac stdio → SIT

| lpac | SIT request | reply payload |
|---|---|---|
| `connect` / `disconnect` | none | |
| `logic_channel_open` (AID) | `0x0247` OPEN_SIM_CHANNEL_WITH_P2: u8 aid_len, aid[16], u8 p2=0 | u32 session, sw1, sw2, u16 len, data |
| `transmit` (APDU) | `0x020f` TRANSMIT_SIM_APDU_CHANNEL: u32 session, cla, ins, p1, p2, p3, u16 len, data | sw1, sw2, u16 len, data |
| `logic_channel_close` | `0x020e` CLOSE_SIM_CHANNEL: u32 session | |

Layouts from `ProtocolSimBuilder` and the `ProtocolSim*Adapter` classes in
`vendor.radio.protocol.sit.stream.so`.
