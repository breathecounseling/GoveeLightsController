"""A pretend WLED controller for testing: answers /json/info over HTTP and counts DDP
frames on UDP 4048.   python tools/fake_wled.py --ip 127.0.0.6 --http-port 8088 --leds 150"""
import argparse
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--ip", default="127.0.0.6")
ap.add_argument("--http-port", type=int, default=8088)
ap.add_argument("--leds", type=int, default=150)
a = ap.parse_args()


class H(BaseHTTPRequestHandler):
    def log_message(self, *x):
        pass

    def do_GET(self):
        body = json.dumps({"ver": "0.15.0", "name": "Fake WLED", "mac": "aabbccddeeff",
                           "leds": {"count": a.leds}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


threading.Thread(target=HTTPServer((a.ip, a.http_port), H).serve_forever, daemon=True).start()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind((a.ip, 4048))
frames, buf = 0, bytearray(a.leds * 3)
while True:
    pkt = s.recv(2000)
    off, ln = int.from_bytes(pkt[4:8], "big"), int.from_bytes(pkt[8:10], "big")
    buf[off:off + ln] = pkt[10:10 + ln]
    if pkt[0] & 0x01:                                  # push flag: frame complete
        frames += 1
        if frames % 20 == 1:
            lit = sum(1 for i in range(0, len(buf), 3) if any(buf[i:i + 3]))
            print(f"frame {frames}: {len(buf) // 3} pixels, {lit} lit, first {tuple(buf[0:3])}", flush=True)
