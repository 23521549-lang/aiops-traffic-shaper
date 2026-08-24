import json
import urllib.error
import urllib.request


class BackendError(Exception):
    pass


def post_json(url: str, payload: dict, headers: dict | None = None,
              opener=urllib.request.urlopen) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise BackendError(f"{e.code}: {e.read().decode(errors='replace')}") from e


def get_json(url: str, headers: dict | None = None, opener=urllib.request.urlopen) -> dict:
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    try:
        with opener(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise BackendError(f"{e.code}: {e.read().decode(errors='replace')}") from e
