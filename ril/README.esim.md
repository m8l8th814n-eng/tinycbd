  sudo apk add cmake curl-dev
  git clone https://github.com/estkme-group/lpac && cd lpac
  cmake -B build -DBUILD_TESTING=OFF -DLPAC_WITH_APDU_PCSC=OFF -DLPAC_WITH_APDU_AT=OFF && cmake --build build
