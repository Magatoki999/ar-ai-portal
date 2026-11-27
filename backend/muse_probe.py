import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def probe():
    key = os.environ.get("META_MODEL_API_KEY", "").strip()
    if not key:
        print("MUSE PROBE: META_MODEL_API_KEY is missing")
        return

    request = Request(
        "https://api.meta.ai/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            models = json.load(response)["data"]
            visible = any(model["id"] == "muse-spark-1.3" for model in models)
            print(f"MUSE PROBE: HTTP {response.status} models={len(models)} spark_visible={visible}")
    except HTTPError as error:
        print(f"MUSE PROBE: HTTP {error.code}")
    except (URLError, TimeoutError, ValueError, KeyError, TypeError) as error:
        print(f"MUSE PROBE: {type(error).__name__}")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK\n")


if __name__ == "__main__":
    probe()
    HTTPServer(("0.0.0.0", int(os.environ["PORT"])), HealthHandler).serve_forever()
