"""Headless Chrome screenshots of /positioning via CDP (own profile, port 9565)."""
import base64
import json
import subprocess
import sys
import time
import urllib.request

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT = "/Users/maxbelov/Projects/wt/positioning-data/docs/evidence/positioning/"
PROFILE = "/Users/maxbelov/.hermes/cache/scratch/positioning-chrome-profile"
shots = [("positioning_default.png", "http://127.0.0.1:5092/positioning", 1500, 1500),
         ("positioning_eth.png", "http://127.0.0.1:5092/positioning?cards=binance:ETHUSDT,bybit:BTCUSDT,hyperliquid:ETH", 1500, 1500)]
if len(sys.argv) > 1:
    shots = [s for s in shots if s[0] in sys.argv[1:]]

proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9565", f"--user-data-dir={PROFILE}",
                         "--no-first-run", "--no-default-browser-check", "--hide-scrollbars", "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9565/json").read())
            break
        except OSError:
            time.sleep(0.2)
    from ws_client import WebSocket  # noqa: E402
    page = [t for t in tabs if t["type"] == "page"][0]
    ws = WebSocket.connect(page["webSocketDebuggerUrl"])
    ws.settimeout(60)
    n = [0]

    def cdp(method, **params):
        n[0] += 1
        ws.send_text(json.dumps({"id": n[0], "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == n[0]:
                return msg.get("result", {})

    for name, url, w, h in shots:
        cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
        cdp("Page.navigate", url=url)
        time.sleep(9)
        txt = cdp("Runtime.evaluate", expression="document.querySelectorAll('.pp').length + ' panels; ' + "
                  "Array.from(document.querySelectorAll('.pp-head')).map(e=>e.textContent).join(' | ')")
        print(name, txt.get("result", {}).get("value"))
        data = cdp("Page.captureScreenshot", format="png", captureBeyondViewport=True)["data"]
        open(OUT + name, "wb").write(base64.b64decode(data))
    ws.close()
finally:
    proc.terminate()
    proc.wait(timeout=10)
