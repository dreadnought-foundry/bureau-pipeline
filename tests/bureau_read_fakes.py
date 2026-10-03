"""A fake read door and a fake OIDC issuer, on 127.0.0.1 (Stage 2 BP-2).

Two jobs:

1. The unit tests here drive `scripts/bureau_read.py` and `scripts/reconcile.py`
   through REAL HTTP against these servers. Nothing in this repository's tests
   calls the console or Linear.

2. THE CROSS-REPO HARNESS HOOK (Stage 2 items 46/47). agent-bureau's
   `make stage2-e2e` runs this repo's real `reconcile.py` against the console's
   real door, with a fake issuer the console is told to trust under
   `CONSOLE_ENV=test`. The client needs nothing special for that — it is
   pointed by environment alone, and `door_env()` below is the one place that
   environment is spelled:

     BUREAU_READ                       off | shadow | on
     BUREAU_READ_URL                   the door's origin (http only to loopback)
     BUREAU_READ_AUDIENCE              the audience the door is configured with
     ACTIONS_ID_TOKEN_REQUEST_URL      the issuer's token endpoint (`FakeIssuer.url`)
     ACTIONS_ID_TOKEN_REQUEST_TOKEN    the request bearer the endpoint checks
     BUREAU_PIPELINE_REF               the ref the scripts were checked out at
     GITHUB_REPOSITORY                 the caller the envelope must name

   `FakeIssuer` mints tokens with whatever claims a scenario sets. It signs
   with a caller-supplied `signer(signing_input: bytes) -> bytes` (the harness
   passes a real RS256 signer the console's test JWKS trusts); without one the
   signature is a fixed placeholder, which is enough for this client — it never
   verifies a token, the door does.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCHEMA = "bureau-read/1"


def _b64(obj) -> str:
    raw = obj if isinstance(obj, bytes) else json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_jwt(claims: dict, *, kid: str = "test-key", alg: str = "RS256", signer=None) -> str:
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    signing_input = f"{_b64(header)}.{_b64(claims)}".encode()
    signature = signer(signing_input) if signer else b"not-a-real-signature"
    return f"{signing_input.decode()}.{_b64(signature)}"


def default_claims(**over) -> dict:
    now = int(time.time())
    claims = {
        "iss": "https://token.actions.githubusercontent.com",
        "aud": "https://app.agent-bureau.com/pipeline-read",
        "repository": "dreadnought-foundry/portico",
        "repository_id": "1",
        "repository_owner_id": "2",
        "job_workflow_ref": (
            "dreadnought-foundry/bureau-pipeline/.github/workflows/"
            "reconcile.yml@refs/tags/stable"),
        "sub": "repo:dreadnought-foundry/portico:ref:refs/heads/main",
        "event_name": "schedule",
        "jti": f"jti-{now}",
        "iat": now,
        "nbf": now,
        "exp": now + 300,
    }
    claims.update(over)
    return claims


class _Server:
    """A ThreadingHTTPServer on an ephemeral loopback port, run in a thread."""

    def __init__(self, handler_cls):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.httpd.owner = self
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class _IssuerHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):  # noqa: N802
        owner = self.server.owner
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        owner.requests.append({"path": self.path, "headers": dict(self.headers),
                               "audience": (query.get("audience") or [None])[0]})
        if self.headers.get("Authorization") != f"Bearer {owner.request_token}":
            self._send(403, {"message": "bad request token"})
            return
        if owner.status != 200:
            self._send(owner.status, {"message": "issuer refused"})
            return
        claims = default_claims(**owner.claims)
        if query.get("audience"):
            claims["aud"] = query["audience"][0]
        self._send(200, {"value": make_jwt(claims, signer=owner.signer)})

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeIssuer(_Server):
    """The runner's OIDC token endpoint, shaped like ACTIONS_ID_TOKEN_REQUEST_URL."""

    def __init__(self, *, request_token: str = "request-token", signer=None, **claims):
        super().__init__(_IssuerHandler)
        self.request_token = request_token
        self.signer = signer
        self.claims = claims
        self.status = 200
        self.requests: list[dict] = []

    @property
    def token_url(self) -> str:
        return f"{self.url}/token?api-version=2.0"


class _DoorHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):  # noqa: N802
        owner = self.server.owner
        split = urllib.parse.urlsplit(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(split.query).items()}
        owner.requests.append({"path": split.path, "query": query,
                               "headers": {k.lower(): v for k, v in self.headers.items()}})
        prefix = "/api/v1/pipeline"
        endpoint = split.path[len(prefix):] if split.path.startswith(prefix) else split.path
        answer = owner.answer(endpoint, query)
        if callable(answer):
            answer = answer(endpoint, query, self.headers)
        status, body, delay = answer if len(answer) == 3 else (*answer, 0)
        if delay:
            time.sleep(delay)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):  # the client gave up
            pass


class FakeDoor(_Server):
    """The console's read door, answering from a `world` of cards.

    `world` maps identifier → a full node (CARD_FIELDS + inverseRelations).
    `routes` overrides an endpoint with `(status, body[, delay])` or a callable.
    """

    def __init__(self, world: dict | None = None, *, as_of: str = "2026-10-02T20:00:00.000Z",
                 age: float = 5.0, relations_as_of: str = "2026-10-02T19:55:00.000Z",
                 repository: str = "dreadnought-foundry/portico", held_lanes=None):
        super().__init__(_DoorHandler)
        self.world = dict(world or {})
        self.as_of = as_of
        self.age = age
        self.relations_as_of = relations_as_of
        self.repository = repository
        self.held_lanes = set(held_lanes) if held_lanes is not None else None
        self.routes: dict = {}
        self.requests: list[dict] = []

    def envelope(self, nodes=None, *, verdict="FRESH", reason=None, lanes=None,
                 nodes_key="issues") -> dict:
        env = {
            "schema": SCHEMA,
            "verdict": verdict,
            "freshness": {
                "as_of": self.as_of, "age_seconds": self.age, "basis": "record",
                "relations_as_of": self.relations_as_of, "reason": reason,
            },
            "caller": {"repository": self.repository, "slug": self.repository.split("/")[-1],
                       "tenant": "00000000-0000-0000-0000-000000000001", "scope": "repo"},
            "viewer": {"id": "fleet-user"},
            nodes_key: ({"nodes": nodes} if verdict == "FRESH" else None),
        }
        if lanes is not None:
            env["lanes"] = lanes
        return env

    def unknown(self, reason: str) -> tuple:
        return 200, self.envelope(verdict="UNKNOWN", reason=reason)

    def answer(self, endpoint: str, query: dict):
        for key in (endpoint, endpoint.split("/")[1] if endpoint.count("/") > 1 else None):
            if key in self.routes:
                return self.routes[key]
        if endpoint == "/board":
            lanes = [x for x in query.get("lanes", "").split(",") if x]
            if self.held_lanes is not None and not set(lanes) <= self.held_lanes:
                return self.unknown("lane-not-held")
            nodes = [self._shaped(n, query) for n in self.world.values()
                     if n["state"]["name"] in lanes]
            return 200, self.envelope(nodes, lanes=lanes)
        if endpoint == "/cards":
            ids = [x for x in query.get("ids", "").split(",") if x]
            if any(i not in self.world for i in ids):
                return 404, {"error": {"code": "NOT_FOUND"}}
            return 200, self.envelope([self._shaped(self.world[i], query) for i in ids])
        if endpoint.startswith("/cards/") and endpoint.endswith("/dependents"):
            # AB-1's shape: the cards `ident` blocks, in the lanes asked (every
            # held lane when none is), each a full board node.
            ident = endpoint.split("/")[2]
            if ident not in self.world:
                return 404, {"error": {"code": "NOT_FOUND"}}
            lanes = [x for x in query.get("lanes", "").split(",") if x]
            nodes = [self._shaped(n, query) for n in self.world.values()
                     if (not lanes or n["state"]["name"] in lanes)
                     and any(r.get("type") == "blocks" and r["issue"]["identifier"] == ident
                             for r in ((n.get("inverseRelations") or {}).get("nodes") or []))]
            return 200, self.envelope(nodes)
        if endpoint == "/workflow-states":
            return 200, self.envelope(
                [{"id": "s1", "name": "Todo", "type": "unstarted"}],
                nodes_key="workflowStates")
        return 404, {"error": {"code": "NOT_FOUND"}}

    @staticmethod
    def _shaped(node: dict, query: dict) -> dict:
        out = dict(node)
        if query.get("relations") == "0":
            out.pop("inverseRelations", None)
        else:
            out.setdefault("inverseRelations",
                           {"pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": []})
        return out

    def dependents_node(self, ident: str) -> dict | None:
        card = self.world.get(ident)
        if card is None:
            return None
        blocks = [
            {"type": "blocks", "issue": {"identifier": ident},
             "relatedIssue": {"identifier": other["identifier"],
                              "state": {"name": other["state"]["name"]}}}
            for other in self.world.values()
            for rel in ((other.get("inverseRelations") or {}).get("nodes") or [])
            if rel.get("type") == "blocks" and rel["issue"]["identifier"] == ident
        ]
        parent = card.get("parent")
        parent_node = None
        if parent:
            kids = [c for c in self.world.values()
                    if (c.get("parent") or {}).get("identifier") == parent["identifier"]]
            parent_node = {
                "identifier": parent["identifier"],
                "state": {"name": parent["state"]["name"]},
                "children": {"pageInfo": {"hasNextPage": False},
                             "nodes": [{"identifier": k["identifier"],
                                        "state": {"name": k["state"]["name"]}} for k in kids]},
            }
        return {"identifier": ident, "state": {"name": card["state"]["name"]},
                "relations": {"pageInfo": {"hasNextPage": False}, "nodes": blocks},
                "parent": parent_node}

    def asked(self, endpoint: str) -> list[dict]:
        return [r for r in self.requests if r["path"].endswith(endpoint)]


def door_env(*, door_url: str, issuer: FakeIssuer | None = None, mode: str = "on",
             audience: str = "https://app.agent-bureau.com/pipeline-read",
             pipeline_ref: str | None = "stable",
             repository: str = "dreadnought-foundry/portico") -> dict:
    """The environment that points the client at a door and an issuer."""
    env = {
        "BUREAU_READ": mode,
        "BUREAU_READ_URL": door_url,
        "BUREAU_READ_AUDIENCE": audience,
        "GITHUB_REPOSITORY": repository,
    }
    if pipeline_ref is not None:
        env["BUREAU_PIPELINE_REF"] = pipeline_ref
    if issuer is not None:
        env["ACTIONS_ID_TOKEN_REQUEST_URL"] = issuer.token_url
        env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"] = issuer.request_token
    return env


def card(ident: str, lane: str, *, labels=(), parent=None, parent_lane="In Progress",
         updated="2026-10-02T19:00:00.000Z", created="2026-09-01T00:00:00.000Z",
         comments=(), blockers=(), relations_partial=False, title=None,
         children=False, priority=0, description="") -> dict:
    """One node in the door's full shape (CARD_FIELDS + inverseRelations).

    `comments` are bodies, oldest first; the node carries them newest-first,
    the way Linear's `first:` window does. `blockers` are `(identifier, lane)`.
    """
    nodes = [
        {"body": body, "createdAt": f"2026-09-0{1 + i % 9}T00:00:{i % 60:02d}.000Z",
         "user": {"id": "fleet-user"}}
        for i, body in enumerate(comments)
    ]
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": title or f"Card {ident}",
        "description": description,
        "createdAt": created,
        "updatedAt": updated,
        "priority": priority,
        "state": {"name": lane},
        "labels": {"nodes": [{"name": name} for name in labels]},
        "parent": ({"identifier": parent, "state": {"name": parent_lane}} if parent else None),
        "children": {"nodes": [{"id": "kid"}] if children else []},
        "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                     "nodes": list(reversed(nodes))},
        "inverseRelations": {
            "pageInfo": {"hasNextPage": relations_partial, "endCursor": None},
            "nodes": [{"type": "blocks", "issue": {"identifier": b, "state": {"name": s}}}
                      for b, s in blockers],
        },
    }
