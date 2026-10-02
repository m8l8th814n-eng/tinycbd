sudo apk add cmake curl-dev
# grab lpac
git clone https://github.com/estkme-group/lpac && cd lpac
 cmake -B build -DBUILD_TESTING=OFF -DLPAC_WITH_APDU_PCSC=OFF -DLPAC_WITH_APDU_AT=OFF && cmake --build build
cd build
# remove lpac if you installed it from apk.
sudo apk del lpac
# install 
sudo make install


# use esim.py
     sudo ./esim.py chip info
     sudo ./esim.py profile list
     sudo ./esim.py profile download -a 'LPA:1$smdp.example$MATCHING-ID'
     sudo ./esim.py profile enable <iccid>
     sudo ./esim.py profile delete <iccid>
     sudo DEBUG=1 ./esim.py chip info  # trace on stderr
      
