# Smart Logistics Sorter — lệnh tiện ích (chạy từ gốc repo)
PY ?= python

.PHONY: gen-protocol test-pi test-c build-esp32 flash-esp32 sim test-ota-app lint

gen-protocol:            ## Sinh protocol_gen.h và protocol_gen.py từ protocol.yaml (P1)
	$(PY) shared/protocol/gen.py

test-pi: gen-protocol    ## Chạy toàn bộ pytest của phần mềm Pi
	cd pi && $(PY) -m pytest -q

test-c:                  ## Test codec C trên máy host bằng gcc (cần gcc)
	gcc -std=c11 -Wall -Wextra -O2 -Iesp32/components/protocol/include \
	    esp32/components/protocol/protocol.c esp32/components/protocol/test/host_test.c \
	    -o build/host_test && ./build/host_test shared/protocol/test_vectors.json

build-esp32: gen-protocol ## Build firmware ESP32 (cần ESP-IDF v5.x đã export)
	cd esp32 && idf.py build

flash-esp32:             ## Nạp firmware (PORT=/dev/ttyUSB0)
	cd esp32 && idf.py -p $(PORT) flash monitor

sim:                     ## Chạy sorter-core ở chế độ mô phỏng trên laptop
	cd pi && $(PY) -m sorter.main --sim --config config.sim.yaml

test-ota-app:            ## Test app Secure OTA
	cd ota-app && $(PY) -m pytest -q

lint:
	$(PY) -m pyflakes pi/sorter ota-app/ota_app || true
