"""Regression pin (DRE-6839): a create is never sent to Linear twice blind.

Origin (2026-10-10, about 19:00 PT, the operator's machine): one
`linear_ops.py oneoff` printed

    transient network fault, retried once: The read operation timed out
    created DRE-6796 ...

and the first attempt had already made DRE-6795 — same title, same labels,
same opening text. A bare `TimeoutError` is a fault while WAITING for the
answer (`urlopen` wraps a connect or send fault in a `URLError`), so the
request had been sent in full and Linear had accepted it. The one-shot retry
(DRE-3087) was written for the sweep's reads, where a second send is harmless;
a create sent twice makes a second thing.

So a create passes `gql` a `landed` look. On a transient fault the seam
prints its usual line, waits its usual backoff, and asks the look what the
first attempt made before it sends anything again: something found is the
answer, nothing found is one more send, a look that fails is raised.

Every test here runs over `SmallLinear`, a stand-in for `urlopen` that keeps
the cards and comments it was asked to make and answers the looks from what it
holds. It can be told to accept a request and then raise in place of the
answer (the incident), or to raise before accepting it. Nothing reaches the
network and no test needs a Linear key.
"""

import ast
import io
import json
import os
import re
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import linear_ops  # noqa: E402

ME = "user-pipeline"
SOMEONE_ELSE = "user-person"
TITLE = "bureau-pipeline: the widget is built once"
BODY = (
    "Build the widget.\n\n"
    "**Blocked by:** DRE-100\n\n"
    "## Acceptance criteria\n- [ ] it works\n"
)
LABELS = ("--label", "repo:bureau-pipeline", "--label", "agent:engineer")


def _read_timeout():
    """The 2026-10-10 fault: the answer never came back."""
    return TimeoutError("The read operation timed out")


def _connect_reset():
    """A fault while connecting — urllib wraps it, so the request never left."""
    return urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer"))


def _http(code):
    return urllib.error.HTTPError(
        linear_ops.API, code, "reason", {}, io.BytesIO(b'{"errors": [{"message": "gateway"}]}')
    )


class _Resp:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SmallLinear:
    """A small Linear behind `urlopen`: it holds cards, comments, relations
    and labels, makes what a mutation asks for, and answers the reads from
    what it holds. `sent` is every request it received, by operation, which is
    how "sent once" is measured — a caller's answer cannot tell one send from
    two.

    The card look is answered on the TITLE alone: which of those cards are
    this key's own, inside the window and under the right parent is the
    code's reading, and that reading is what the tests exercise."""

    def __init__(self):
        self.cards: list[dict] = []
        self.comments: list[dict] = []
        self.relations: list[tuple[str, str]] = []
        self.labels: dict[str, str] = {}
        self.sent: list[str] = []
        self._faults: dict[str, list[tuple[str, BaseException]]] = {}
        self._next = 6795
        self.add_card("DRE-100", "the blocker", creator=SOMEONE_ELSE,
                      created_at="2026-01-01T00:00:00Z")
        self.add_card("DRE-50", "[EPIC] the parent", creator=SOMEONE_ELSE,
                      created_at="2026-01-01T00:00:00Z",
                      labels=["repo:bureau-pipeline", "agent:engineer"])
        self.add_card("DRE-51", "[EPIC] another parent", creator=SOMEONE_ELSE,
                      created_at="2026-01-01T00:00:00Z",
                      labels=["repo:bureau-pipeline", "agent:engineer"])

    # ── what the tests arrange ────────────────────────────────────────────
    def add_card(self, identifier, title, *, creator=ME, created_at=None,
                 parent=None, labels=()):
        card = {
            "id": f"id-{identifier}", "identifier": identifier, "title": title,
            "url": f"https://linear.app/dreadnoughtfoundry/issue/{identifier}",
            "creator": creator, "createdAt": created_at or _now(),
            "parent": parent, "labels": list(labels),
        }
        self.cards.append(card)
        return card

    def fault(self, op: str, when: str, exc: BaseException) -> None:
        """The next `op` raises `exc` — `before` it is accepted, or `after`
        it was accepted and its answer lost."""
        self._faults.setdefault(op, []).append((when, exc))

    def titled(self, title=TITLE) -> list[dict]:
        return [c for c in self.cards if c["title"] == title]

    def card(self, identifier) -> dict | None:
        return next((c for c in self.cards
                     if identifier in (c["identifier"], c["id"])), None)

    # ── urlopen ───────────────────────────────────────────────────────────
    def __call__(self, req, timeout=None):
        body = json.loads(req.data)
        query = " ".join(body["query"].split())
        variables = body.get("variables") or {}
        op = self._op(query)
        self.sent.append(op)
        queued = self._faults.get(op) or []
        fault = queued.pop(0) if queued else None
        if fault and fault[0] == "before":
            raise fault[1]
        data = self._answer(op, query, variables)
        if fault:
            raise fault[1]
        return _Resp(json.dumps({"data": data}).encode())

    @staticmethod
    def _op(q: str) -> str:
        for name in ("issueCreate", "commentCreate", "issueRelationCreate",
                     "issueLabelCreate"):
            if f"{name}(" in q:
                return name
        if "teams(filter" in q:
            return "teams"
        if "workflowStates" in q:
            return "states"
        if "team(id: $teamId)" in q:
            return "team-labels"
        if "comments(first" in q and "issue(id: $id)" in q:
            return "thread"
        if "issues(filter" in q:
            return "look"
        if "issue(id: $id) { id identifier title team" in q:
            return "get-issue"
        if "issue(id: $id) { labels" in q:
            return "issue-labels"
        if "viewer" in q:
            return "viewer"
        raise AssertionError(f"SmallLinear does not know this request: {q[:200]}")

    def _answer(self, op, q, v):
        if op == "teams":
            return {"teams": {"nodes": [{"id": "team-1"}]}}
        if op == "states":
            return {"workflowStates": {"nodes": [
                {"id": "st-planning", "name": "Planning", "type": "backlog"},
                {"id": "st-backlog", "name": "Backlog", "type": "backlog"},
            ]}}
        if op == "team-labels":
            return {"team": {"labels": {"nodes": [
                {"id": lid, "name": name} for name, lid in self.labels.items()]}}}
        if op == "issueLabelCreate":
            name = v["input"]["name"]
            self.labels[name] = f"lbl-{name}"
            return {"issueLabelCreate": {"issueLabel": {"id": self.labels[name]}}}
        if op == "get-issue":
            c = self.card(v["id"])
            if c is None:
                return {"issue": None}
            return {"issue": {
                "id": c["id"], "identifier": c["identifier"], "title": c["title"],
                "team": {"id": "team-1"},
                "state": {"id": "st-backlog", "name": "Backlog", "type": "backlog"},
                "labels": {"nodes": [{"name": n} for n in c["labels"]]},
                "children": {"nodes": []},
            }}
        if op == "issue-labels":
            c = self.card(v["id"])
            return {"issue": {"labels": {"nodes": [{"name": n} for n in c["labels"]]}}}
        if op == "issueCreate":
            inp = v["input"]
            ident = f"DRE-{self._next}"
            self._next += 1
            parent = self.card(inp["parentId"])["id"] if inp.get("parentId") else None
            c = self.add_card(ident, inp["title"], creator=ME, parent=parent)
            return {"issueCreate": {"success": True, "issue": {
                "id": c["id"], "identifier": c["identifier"], "url": c["url"]}}}
        if op == "issueRelationCreate":
            inp = v["input"]
            self.relations.append((inp["issueId"], inp["relatedIssueId"]))
            return {"issueRelationCreate": {"success": True}}
        if op == "look":
            title = v["title"]
            return {"viewer": {"id": ME}, "issues": {"nodes": [
                {"id": c["id"], "identifier": c["identifier"], "url": c["url"],
                 "title": c["title"], "createdAt": c["createdAt"],
                 "creator": {"id": c["creator"]},
                 "parent": {"id": c["parent"]} if c["parent"] else None}
                for c in self.titled(title)]}}
        if op == "commentCreate":
            inp = v["input"]
            self.comments.append({"issue": inp["issueId"], "body": inp["body"],
                                  "createdAt": _now(), "user": ME})
            return {"commentCreate": {"success": True}}
        if op == "thread":
            c = self.card(v["id"])
            nodes = [{"body": m["body"], "createdAt": m["createdAt"],
                      "user": {"id": m["user"]}}
                     for m in self.comments if m["issue"] == c["id"]]
            return {"viewer": {"id": ME}, "issue": {"comments": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": list(reversed(nodes))}}}
        if op == "viewer":
            return {"viewer": {"id": ME}}
        raise AssertionError(op)


@pytest.fixture
def linear(monkeypatch):
    world = SmallLinear()
    monkeypatch.setattr(linear_ops.urllib.request, "urlopen", world)
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.delenv("BUREAU_READ", raising=False)
    return world


@pytest.fixture
def body_file(tmp_path):
    path = tmp_path / "card.md"
    path.write_text(BODY)
    return str(path)


def _created_line(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln.startswith("created ")]
    assert len(lines) == 1, f"expected one created line, got {out!r}"
    return lines[0]


# ── the incident: the first create landed and its answer was lost ───────────
def test_oneoff_whose_answer_was_lost_adopts_the_card_it_made(linear, body_file, capsys):
    linear.fault("issueCreate", "after", _read_timeout())

    issue = linear_ops.cmd_oneoff(TITLE, body_file, *LABELS)

    made = linear.titled()
    assert len(made) == 1, f"two cards with one title: {[c['identifier'] for c in made]}"
    assert linear.sent.count("issueCreate") == 1, "the create was sent again"
    card = made[0]
    assert issue["identifier"] == card["identifier"]
    assert _created_line(capsys.readouterr().out).startswith(
        f"created {card['identifier']} {card['url']}")
    assert linear.relations == [("id-DRE-100", card["id"])], \
        "the blocker relation must be made on the adopted card"


def test_subissue_whose_answer_was_lost_adopts_its_child(linear, body_file, capsys):
    linear.fault("issueCreate", "after", _read_timeout())

    issue = linear_ops.cmd_subissue("DRE-50", TITLE, body_file)

    children = [c for c in linear.titled() if c["parent"] == "id-DRE-50"]
    assert len(linear.titled()) == 1 and len(children) == 1
    assert linear.sent.count("issueCreate") == 1
    assert issue["identifier"] == children[0]["identifier"]
    assert _created_line(capsys.readouterr().out).startswith(
        f"created {children[0]['identifier']} ")
    assert linear.relations == [("id-DRE-100", children[0]["id"])]


def test_create_card_whose_answer_was_lost_returns_the_card_it_made(linear, capsys):
    linear.fault("issueCreate", "after", _read_timeout())

    issue = linear_ops.create_card(TITLE, BODY, repo_slug="bureau-pipeline")

    made = linear.titled()
    assert len(made) == 1
    assert linear.sent.count("issueCreate") == 1
    assert issue["identifier"] == made[0]["identifier"]
    assert issue["url"] == made[0]["url"]
    assert _created_line(capsys.readouterr().out).startswith(
        f"created {made[0]['identifier']} ")


# ── a fault before Linear took it: the look finds nothing, sent once more ───
@pytest.mark.parametrize("fault", [_connect_reset, lambda: _http(503)],
                         ids=["connection-reset", "http-503"])
def test_a_create_that_never_landed_is_sent_once_more(linear, body_file, fault):
    linear.fault("issueCreate", "before", fault())

    issue = linear_ops.cmd_oneoff(TITLE, body_file, *LABELS)

    assert len(linear.titled()) == 1
    assert linear.sent.count("issueCreate") == 2
    creates = [i for i, op in enumerate(linear.sent) if op == "issueCreate"]
    assert "look" in linear.sent[creates[0]:creates[1]], \
        "the second send must come after a look found nothing"
    assert issue["identifier"] == linear.titled()[0]["identifier"]


# ── the look adopts only its own card ───────────────────────────────────────
@pytest.mark.parametrize("decoy", ["another-identity", "before-the-window", "another-parent"])
def test_the_look_adopts_only_its_own_card(linear, body_file, decoy):
    """Each decoy is the ONE card with this title the look can see, so a look
    that ignored the rule it breaks would adopt it."""
    if decoy == "another-identity":
        linear.add_card("DRE-900", TITLE, creator=SOMEONE_ELSE, parent="id-DRE-50")
    elif decoy == "before-the-window":
        an_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        linear.add_card("DRE-900", TITLE, created_at=an_hour_ago, parent="id-DRE-50")
    else:
        linear.add_card("DRE-900", TITLE, parent="id-DRE-51")
    linear.fault("issueCreate", "before", _read_timeout())

    issue = linear_ops.cmd_subissue("DRE-50", TITLE, body_file)

    assert issue["identifier"] != "DRE-900", f"adopted a card it did not make ({decoy})"
    assert linear.sent.count("issueCreate") == 2
    assert [c["identifier"] for c in linear.titled()] == ["DRE-900", issue["identifier"]]


def test_two_matching_cards_adopt_neither_and_name_both(linear, body_file):
    linear.add_card("DRE-900", TITLE)
    linear.fault("issueCreate", "after", _read_timeout())

    with pytest.raises(linear_ops.LinearError) as raised:
        linear_ops.cmd_oneoff(TITLE, body_file, *LABELS)

    made = [c["identifier"] for c in linear.titled()]
    assert len(made) == 2
    for ident in made:
        assert ident in str(raised.value)
    assert linear.sent.count("issueCreate") == 1
    assert linear.relations == [], "no blocker relation on a card nobody adopted"


def test_a_look_that_fails_is_raised_and_nothing_is_sent_again(linear, body_file):
    linear.fault("issueCreate", "before", _read_timeout())
    linear.fault("look", "before", _http(500))

    with pytest.raises(linear_ops.LinearError):
        linear_ops.cmd_oneoff(TITLE, body_file, *LABELS)

    assert linear.sent.count("issueCreate") == 1
    assert linear.sent.count("look") == 1
    assert linear.titled() == []


# ── comments ────────────────────────────────────────────────────────────────
def _thread_bodies(linear, identifier="DRE-100"):
    card = linear.card(identifier)
    return [m["body"] for m in linear.comments if m["issue"] == card["id"]]


def test_a_comment_whose_answer_was_lost_is_not_posted_twice(linear, capsys):
    linear.fault("commentCreate", "after", _read_timeout())

    assert linear_ops.cmd_comment("DRE-100", "🔁 turn-exhaustion-requeue") is None

    assert _thread_bodies(linear) == ["🔁 turn-exhaustion-requeue"]
    assert linear.sent.count("commentCreate") == 1
    assert "commented on DRE-100" in capsys.readouterr().out


def test_a_comment_that_never_landed_is_posted_once(linear):
    linear.fault("commentCreate", "before", _read_timeout())

    assert linear_ops.cmd_comment("DRE-100", "🔁 turn-exhaustion-requeue") is None

    assert _thread_bodies(linear) == ["🔁 turn-exhaustion-requeue"]
    assert linear.sent.count("commentCreate") == 2


def test_the_comment_look_ignores_an_older_or_anothers_comment(linear):
    """The same words posted by a person, or by this key an hour ago, are not
    the comment this call made."""
    card = linear.card("DRE-100")
    an_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    linear.comments.append({"issue": card["id"], "body": "same words",
                            "createdAt": _now(), "user": SOMEONE_ELSE})
    linear.comments.append({"issue": card["id"], "body": "same words",
                            "createdAt": an_hour_ago, "user": ME})
    linear.fault("commentCreate", "before", _read_timeout())

    linear_ops.cmd_comment("DRE-100", "same words")

    assert linear.sent.count("commentCreate") == 2
    assert _thread_bodies(linear).count("same words") == 3


# ── what is said, and where ─────────────────────────────────────────────────
def test_an_adopted_create_says_so_on_stderr_only(linear, body_file, capsys):
    linear.fault("issueCreate", "after", _read_timeout())

    issue = linear_ops.cmd_oneoff(TITLE, body_file, *LABELS)

    out, err = capsys.readouterr()
    assert "transient network fault, retried once: The read operation timed out" in err
    assert (f"create: the first attempt landed as {issue['identifier']}; "
            "not sent again") in err.splitlines()
    assert "first attempt made nothing" not in err
    assert out.splitlines() == [_created_line(out)], "stdout carries the created line and nothing new"
    assert "landed" not in out


def test_a_resent_create_says_so_on_stderr_only(linear, body_file, capsys):
    linear.fault("issueCreate", "before", _connect_reset())

    linear_ops.cmd_oneoff(TITLE, body_file, *LABELS)

    out, err = capsys.readouterr()
    assert "transient network fault, retried once: " in err
    assert "create: the first attempt made nothing; sent again" in err.splitlines()
    assert "landed as" not in err
    assert out.splitlines() == [_created_line(out)]


# ── the seam itself ─────────────────────────────────────────────────────────
def test_landed_is_not_asked_when_nothing_faulted(linear):
    asked = []
    linear_ops.gql("query { viewer { id } }", landed=lambda: asked.append(1))
    assert asked == []


def test_landed_is_not_asked_on_a_fault_that_is_not_retried(linear):
    linear.fault("viewer", "before", _http(400))
    asked = []
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql("query { viewer { id } }", landed=lambda: asked.append(1))
    assert asked == []


def test_landed_answer_is_the_calls_answer(linear):
    linear.fault("viewer", "after", _read_timeout())
    answer = {"viewer": {"id": "from-the-look"}}
    assert linear_ops.gql("query { viewer { id } }", landed=lambda: answer) == answer
    assert linear.sent.count("viewer") == 1


def test_the_seam_comment_names_the_case_the_look_cannot_close():
    source = (SCRIPTS / "linear_ops.py").read_text()
    seam = source[source.index("def _send("):source.index("def gql_paged(")]
    assert "still making" in seam and "two" in seam, \
        "the seam must say, in plain words, that a slow answer still makes two"


# ── every creating call chooses ─────────────────────────────────────────────
_CREATE_FIELD = re.compile(r"\b([a-z][A-Za-z]*Create)\s*\(")


def _gql_call_with_landed(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name == "gql" and any(kw.arg == "landed" for kw in node.keywords)


def _creates_in(tree: ast.AST) -> tuple[set[int], list[tuple[int, str, str]]]:
    """(ids of the string nodes sent through `gql(..., landed=...)`, and every
    mutation string naming a `…Create` field as (line, field, kind))."""
    with_landed: set[int] = set()
    for node in ast.walk(tree):
        if _gql_call_with_landed(node) and node.args:
            with_landed.update(id(n) for n in ast.walk(node.args[0])
                               if isinstance(n, ast.Constant))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if not text.startswith("mutation"):
                continue
            for field in _CREATE_FIELD.findall(text):
                found.append((node.lineno, field, id(node)))
    return with_landed, found


def test_every_create_is_sent_with_landed_or_named_as_left_blind():
    blind = linear_ops.BLIND_RETRY_CREATES
    assert set(blind) == {"issueLabelCreate", "issueRelationCreate", "releaseNoteCreate"}
    for field, reason in blind.items():
        assert isinstance(reason, str) and len(reason.split()) >= 4, \
            f"{field} needs one line of reason"

    offenders, guarded, seen = [], set(), set()
    for path in sorted(SCRIPTS.glob("*.py")):
        with_landed, found = _creates_in(ast.parse(path.read_text()))
        for line, field, node_id in found:
            seen.add(field)
            if node_id in with_landed:
                guarded.add(field)
            elif field not in blind:
                offenders.append(f"{path.name}:{line} {field}")
    assert not offenders, (
        "a create sent with no `landed` look and not named in "
        f"BLIND_RETRY_CREATES: {offenders}")
    # Not vacuous: the scan does see the creates, and the guarded ones as such.
    assert {"issueCreate", "commentCreate"} <= guarded
    assert set(blind) <= seen, "a create named as left blind that no script sends"
