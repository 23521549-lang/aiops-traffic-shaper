import hashlib
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
        headers={
            "Content-Type": "application/json",
            # ADR-005. The backend sits behind CloudFront, whose origin access
            # control signs the request to the Lambda function URL but NOT the
            # body, and Lambda refuses unsigned payloads. Without this header
            # the POST is rejected at the edge with a 403 that never reaches
            # the application - so the agent would report the backend as down
            # when the only thing missing is a hash of what it just sent.
            #
            # Hash the exact bytes on the wire, never a re-serialisation: two
            # json.dumps calls are not guaranteed to agree, and a hash of
            # different bytes is indistinguishable from an attack.
            "x-amz-content-sha256": hashlib.sha256(data).hexdigest(),
            **(headers or {}),
        },
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
