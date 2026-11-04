"""A comment Linear refuses for size is posted as numbered parts (DRE-8818).

THE WALL AFTER DRE-8758. Since DRE-8758 a body reaches `linear_ops.py
comment` from a file, so the kernel's 128 KiB cap on one argument no longer
kills the `Mechanical findings` step. The next wall is Linear's own: a note
it refuses for size. The step's `|| echo "::warning title=Mechanical note
not posted::…"` keeps the critics running, but the epic loses its copy of
the list. So `comment` now reads the maximum out of the refusal and posts
the body as numbered parts, each at most that size with its header, each its
own `commentCreate` with its own read-back look, so a retried part is never
doubled. A refusal that names no number is not split and no size is
guessed: the command exits non-zero naming it, as before, and the step's
warning stands. A receipt composed with `--act=<name>` is never split.

WHAT IS KNOWN ABOUT LINEAR'S LIMIT. DRE-8698's round-1 note of 57,370
characters posted in one comment (the card's own account). Whether Linear
accepts 200 KB in one comment is not known before release — the PROOF reads
it from the step's log. No refusal for size has been observed by this run,
no Linear document read here names a limit, and the plan run logs since
DRE-8758 live in the caller repos' Actions, which this run's read token is
refused (HTTP 403, `Resource not accessible by integration`). So the shape
of the refusal is not known either: the stub below answers with the two
shapes the card names — a GraphQL error naming a maximum length, in the
class-validator wording Linear's other input errors use, and an HTTP 413 —
and the classifier in `linear_ops.size_refusal_limit` reads a number from
either.

The CLI tests run the real script in a subprocess with `urlopen` replaced by
a STATEFUL stub installed through a `sitecustomize` on `PYTHONPATH` (the
shape of `tests/test_linear_body_from_file.py`): what a `commentCreate`
posts is kept in a JSON file, and the card's thread is read back from it, so
a test reads the comments through `linear_ops.py dump-comments` — the
script's own thread reader — exactly as Linear would hand them back.
"""

import json
import os
import re
import subprocess  # nosec B404 - runs this repo's own scripts, fixed argv
import sys
import textwrap

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import linear_ops  # noqa: E402

#: Over 200 KiB, and over the platform's whole ARG_MAX too, so the old
#: one-argument form fails on a Mac as it does on the Linux runner.
try:
    _ARG_MAX = os.sysconf("SC_ARG_MAX")
except (AttributeError, ValueError, OSError):
    _ARG_MAX = 0
NOTE_BYTES = max(200 * 1024 + 1, _ARG_MAX + 1)

#: The maximum the stub's refusal names, in characters.
MAX = 4_000

SITECUSTOMIZE = textwrap.dedent('''
    import io, json, os, urllib.error, urllib.request
    from datetime import datetime, timezone

    def _card(comments):
        return {
            "id": "issue-1", "identifier": "DRE-1", "title": "A card",
            "description": "the card's description",
            "team": {"id": "team-1", "key": "DRE"},
            "state": {"id": "s-progress", "name": "In Progress", "type": "started"},
            "labels": {"nodes": []},
            "children": {"nodes": []},
            # Newest first, as Linear orders a thread.
            "comments": {"nodes": list(reversed(comments)),
                         "pageInfo": {"hasNextPage": False, "endCursor": None}},
            "history": {"nodes": []},
        }

    def _load():
        try:
            with open(os.environ["STUB_STATE"]) as f:
                return json.load(f)
        except FileNotFoundError:
            return {"comments": [], "posts": 0}

    def _save(state):
        with open(os.environ["STUB_STATE"], "w") as f:
            json.dump(state, f)

    class _Resp:
        headers = {}

        def __init__(self, obj):
            self._body = json.dumps(obj).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _refuse(limit):
        shape = os.environ.get("STUB_REFUSAL", "graphql")
        if shape == "graphql":
            return _Resp({"errors": [{
                "message": "Argument Validation Error",
                "extensions": {
                    "code": "INVALID_INPUT", "type": "invalid input",
                    "validationErrors": [{"property": "body", "constraints": {
                        "maxLength": f"body must be shorter than or equal to {limit} characters"}}],
                    "userPresentableMessage": f"body must be shorter than or equal to {limit} characters",
                }}]})
        if shape == "graphql-no-number":
            return _Resp({"errors": [{
                "message": "Comment body is too long",
                "extensions": {"code": "INVALID_INPUT", "type": "invalid input"}}]})
        body = (json.dumps({"error": f"Payload Too Large: the maximum length is {limit:,} characters"})
                if shape == "413" else "<html><body>413 Request Entity Too Large</body></html>")
        raise urllib.error.HTTPError("https://api.linear.app/graphql", 413,
                                     "Payload Too Large", {}, io.BytesIO(body.encode()))

    def urlopen(req, timeout=None):
        payload = json.loads(req.data)
        with open(os.environ["STUB_LOG"], "a") as f:
            f.write(json.dumps(payload) + "\\n")
        state = _load()
        if not payload["query"].lstrip().startswith("mutation"):
            return _Resp({"data": {"issue": _card(state["comments"]), "viewer": {"id": "u1"}}})
        body = payload["variables"]["input"]["body"]
        limit = int(os.environ.get("STUB_MAX_BODY") or 0)
        if limit and len(body) > limit:
            return _refuse(limit)
        state["posts"] += 1
        state["comments"].append({
            "id": f"c{len(state['comments']) + 1}", "body": body,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "user": {"id": "u1"},
        })
        _save(state)
        # The answer to one post is lost after Linear took it: the timeout a
        # retry is sent on, and the case the read-back look exists for.
        if state["posts"] == int(os.environ.get("STUB_LOSE_POST") or 0):
            raise TimeoutError("the answer to this post was lost")
        return _Resp({"data": {"commentCreate": {"success": True}}})

    urllib.request.urlopen = urlopen
''')


def _env(tmp_path, **extra):
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "sitecustomize.py").write_text(SITECUSTOMIZE)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(stub),
        "LINEAR_API_KEY": "lin_api_test",
        "LINEAR_IDENTITY": "fleet",
        "GITHUB_ACTIONS": "true",
        "STUB_LOG": str(tmp_path / "requests.jsonl"),
        "STUB_STATE": str(tmp_path / "linear-state.json"),
    }
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _run_cli(tmp_path, *argv, stdin=None, **extra):
    return subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, os.path.join(SCRIPTS, "linear_ops.py"), *argv],
        input=stdin, capture_output=True, text=True, env=_env(tmp_path, **extra),
        timeout=120, check=False,
    )


def _mutations(tmp_path):
    log = tmp_path / "requests.jsonl"
    if not log.exists():
        return []
    sent = [json.loads(line) for line in log.read_text().splitlines() if line]
    return [p for p in sent if p["query"].lstrip().startswith("mutation")]


def _thread(tmp_path):
    """The card's comments, read back through the script's own reader."""
    proc = _run_cli(tmp_path, "dump-comments", "DRE-1")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.splitlines()[-1])


def _comment_lines(proc):
    return [ln for ln in proc.stdout.splitlines() if ln.startswith("commented on")]


def _big_note(size=NOTE_BYTES) -> str:
    line = ("- LE59 | Drawer: the waiting note 'Waiting on …' | `stateNote() .k-note.quiet` "
            "| M10 Notice | DRE-8701 → deferred — built by the Loops page\n")
    out = []
    total = 0
    while total < size:
        out.append(line)
        total += len(line.encode())
    return "Mechanical findings\n" + "".join(out) + "\n\n"


def _refused_note() -> str:
    """Several times MAX, with one line longer than MAX on its own — cut at
    the size — and characters outside ASCII, so a byte is not a character."""
    lines = [f"- row {i:03d} — a surface line with an em dash and “quotes”\n" for i in range(300)]
    lines.insert(120, "x" * (MAX * 2 + 17) + " ✓ 🧭 the one long line\n")
    return "Mechanical findings\n" + "".join(lines)


_HEADER_RE = re.compile(r"\APart (\d+) of (\d+) — [^\n]*\n")


def _unheaded(parts):
    """The parts' own text with each header line removed, and the (i, n) each
    header named."""
    named, text = [], []
    for part in parts:
        m = _HEADER_RE.match(part)
        assert m, f"a part opens with its header: {part[:120]!r}"
        named.append((int(m.group(1)), int(m.group(2))))
        assert m.group(0) == linear_ops.PART_HEADER.format(i=m.group(1), n=m.group(2)) + "\n"
        text.append(part[m.end():])
    return named, text


# --- The body read in full, and the old form's wall --------------------------

def test_a_200_kib_body_posts_through_body_file_and_reads_back_whole(tmp_path):
    note = tmp_path / "plan-mechanical.md"
    text = _big_note()
    note.write_text(text, encoding="utf-8")
    assert note.stat().st_size > 200 * 1024
    proc = _run_cli(tmp_path, "comment", "DRE-1", f"--body-file={note}")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert _comment_lines(proc) == ["commented on DRE-1"]
    assert _thread(tmp_path) == [text.rstrip("\n")]

    # The mutation check: the same note through the pre-DRE-8758 form, run
    # by bash into a fresh stub, never reaches Linear.
    old = tmp_path / "old"
    work = old / "work"
    work.mkdir(parents=True)
    os.symlink(ROOT, work / ".bureau-pipeline")
    proc = subprocess.run(  # nosec B603 B607 - the pre-DRE-8758 shell form
        ["bash", "-c",
         'python3 .bureau-pipeline/scripts/linear_ops.py comment "$ID" "$(cat "$F")"'],
        cwd=work, capture_output=True, text=True, timeout=120, check=False,
        env=_env(old, ID="DRE-1", F=str(note)),
    )
    assert proc.returncode == 126 or "Argument list too long" in proc.stderr, (
        proc.returncode, proc.stderr[-2000:])
    assert not _mutations(old)
    assert _thread(old) == []


def test_a_body_from_stdin_still_posts_whole(tmp_path):
    proc = _run_cli(tmp_path, "comment", "DRE-1", "--body-file=-", stdin="from stdin\n\n")
    assert proc.returncode == 0, proc.stderr
    assert _thread(tmp_path) == ["from stdin"]


# --- A size refusal naming a maximum ----------------------------------------

@pytest.mark.parametrize("shape", ["graphql", "413"])
def test_a_size_refusal_naming_a_maximum_posts_numbered_parts(tmp_path, shape):
    note = tmp_path / "note.md"
    text = _refused_note()
    note.write_text(text, encoding="utf-8")
    assert len(text) > MAX * 3
    proc = _run_cli(tmp_path, "comment", "DRE-1", f"--body-file={note}",
                    STUB_MAX_BODY=MAX, STUB_REFUSAL=shape)
    assert proc.returncode == 0, proc.stderr[-2000:]
    parts = _thread(tmp_path)
    n = len(parts)
    assert n > 3
    for part in parts:
        assert len(part) <= MAX, len(part)
    named, text_parts = _unheaded(parts)
    assert named == [(i, n) for i in range(1, n + 1)]
    assert "".join(text_parts) == text.rstrip("\n")
    # The whole body was tried once, then each part once.
    assert len(_mutations(tmp_path)) == 1 + n


def test_a_one_line_longer_than_the_maximum_is_cut_at_the_size(tmp_path):
    proc = _run_cli(tmp_path, "comment", "DRE-1", "y" * (MAX * 3),
                    STUB_MAX_BODY=MAX)
    assert proc.returncode == 0, proc.stderr[-2000:]
    parts = _thread(tmp_path)
    assert all(len(p) <= MAX for p in parts)
    _, text_parts = _unheaded(parts)
    assert "".join(text_parts) == "y" * (MAX * 3)
    # Cut at the size: every part but the last is full.
    assert all(len(p) > MAX - 8 for p in parts[:-1])


def test_parts_split_on_line_boundaries_where_a_line_fits():
    body = "".join(f"line {i:04d} of the note\n" for i in range(2_000)).rstrip("\n")
    parts = linear_ops.comment_parts(body, 1_000)
    assert len(parts) >= 10, "two-digit part numbers are sized too"
    for i, part in enumerate(parts, 1):
        assert len(part.encode()) <= 1_000
        header = linear_ops.PART_HEADER.format(i=i, n=len(parts)) + "\n"
        assert part.startswith(header)
        rest = part[len(header):]
        # Whole lines only: every part but the last ends on a line's end.
        if i < len(parts):
            assert rest.endswith("\n")
        assert all(re.fullmatch(r"line \d{4} of the note", ln) for ln in rest.splitlines())
    assert "".join(p.split("\n", 1)[1] for p in parts) == body


def test_a_retried_part_is_not_doubled(tmp_path):
    """The second part's answer is lost after Linear took it: its own look
    finds it, so it is not sent again and the thread holds each part once."""
    text = _refused_note()
    proc = _run_cli(tmp_path, "comment", "DRE-1", "--body-file=-", stdin=text,
                    STUB_MAX_BODY=MAX, STUB_LOSE_POST=2)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "not sent again" in proc.stderr
    parts = _thread(tmp_path)
    named, text_parts = _unheaded(parts)
    assert named == [(i, len(parts)) for i in range(1, len(parts) + 1)]
    assert "".join(text_parts) == text.rstrip("\n")


# --- A size refusal naming no number ----------------------------------------

@pytest.mark.parametrize("shape", ["graphql-no-number", "413-no-number"])
def test_a_size_refusal_naming_no_number_is_not_split(tmp_path, shape):
    proc = _run_cli(tmp_path, "comment", "DRE-1", "--body-file=-",
                    stdin=_refused_note(), STUB_MAX_BODY=MAX, STUB_REFUSAL=shape)
    assert proc.returncode != 0
    assert _thread(tmp_path) == [], "nothing is posted"
    assert len(_mutations(tmp_path)) == 1, "no part is tried"
    assert "too long" in proc.stderr.lower() or "413" in proc.stderr, proc.stderr
    assert "names no maximum" in proc.stderr, proc.stderr
    assert not _comment_lines(proc)


# --- A receipt is never split -----------------------------------------------

def test_a_receipt_composed_with_act_is_never_split(tmp_path):
    proc = _run_cli(tmp_path, "comment", "DRE-1", "--body-file=-",
                    "--act=epic-approval-queued", stdin=_refused_note(),
                    STUB_MAX_BODY=MAX)
    assert proc.returncode != 0
    assert _thread(tmp_path) == []
    assert len(_mutations(tmp_path)) == 1, "the receipt is tried whole, once"
    assert not _comment_lines(proc)


# --- The comment-post line ----------------------------------------------------

def test_a_parted_post_prints_one_line_per_part(tmp_path):
    proc = _run_cli(tmp_path, "comment", "DRE-1", "--body-file=-",
                    stdin=_refused_note(), STUB_MAX_BODY=MAX)
    assert proc.returncode == 0, proc.stderr[-2000:]
    n = len(_thread(tmp_path))
    assert _comment_lines(proc) == [f"commented on DRE-1 (part {i} of {n})"
                                    for i in range(1, n + 1)]


def test_a_whole_post_prints_the_line_it_always_has(tmp_path):
    proc = _run_cli(tmp_path, "comment", "DRE-1", "a short body", STUB_MAX_BODY=MAX)
    assert proc.returncode == 0, proc.stderr
    assert _comment_lines(proc) == ["commented on DRE-1"]
    assert _thread(tmp_path) == ["a short body"]
