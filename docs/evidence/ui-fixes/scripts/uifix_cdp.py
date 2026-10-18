"""Tiny CDP driver for headless Chrome on :9574 (ui-fixes worktree, server :5099)."""
import base64
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/Users/maxbelov/Projects/wt/ui-fixes")
from ws_client import WebSocket  # noqa: E402

BASE = "http://127.0.0.1:5099"
EVID = "/Users/maxbelov/Projects/wt/ui-fixes/docs/evidence/ui-fixes/"


class Page:
    def __init__(self, port=9574):
        tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read())
        page = [t for t in tabs if t["type"] == "page"][0]
        self.ws = WebSocket.connect(page["webSocketDebuggerUrl"])
        self.ws.settimeout(120)
        self.n = 0
        self.cdp("Network.enable")
        self.cdp("Network.setCacheDisabled", cacheDisabled=True)

    def cdp(self, method, **params):
        self.n += 1
        self.ws.send_text(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def ev(self, expr):
        r = self.cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            return {"EXC": r["exceptionDetails"].get("exception", {}).get("description", r["exceptionDetails"].get("text"))}
        return r.get("result", {}).get("value")

    def size(self, w=1600, h=1000):
        self.cdp("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)

    def nav(self, path="/ui", wait=5):
        self.cdp("Page.navigate", url=BASE + path)
        time.sleep(wait)

    def shot(self, name):
        data = self.cdp("Page.captureScreenshot", format="png")["data"]
        path = EVID + name
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path
