"""The CI check that refuses any new direct Linear read (DRE-9164).

`scripts/check_linear_reads.py` finds every function in `scripts/` that hands
Linear a read itself, and every workflow step that does, and compares
them with `config/linear-direct-reads.json`, read at the merge base. These
tests pin, in order:

  * this repo's own ledger: every site the scan finds is listed, each row in
    the shape the card wrote, and the check passes on the branch;
  * each transport the scanner recognizes, on throwaway `scripts/` trees — and
    the shapes that are not sites (a mutation, a request to another host,
    `linear_ops`'s own plumbing, a new caller of a listed site);
  * which files the scan walks, and how a nested file's site is named;
  * the workflow transport, on a throwaway workflow and on this repo's own;
  * the four ledger rules, over throwaway git repositories;
  * the Pipeline Tests job, the proof-reach declaration and the docstring.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_linear_reads.py"
LEDGER = ROOT / "config" / "linear-direct-reads.json"
TESTS_YML = ROOT / ".github" / "workflows" / "tests.yml"
PROOF_REACH = ROOT / "config" / "proof-reach.json"

sys.path.insert(0, str(ROOT / "scripts"))

import check_linear_reads as clr  # noqa: E402

THIS_CARD = "DRE-9164"
SUMMARY = re.compile(
    r"^linear-reads-check: (\d+) read site\(s\) found, (\d+) listed \((\d+) kept\), "
    r"(\d+) pending, (\d+) new, (\d+) gone$", re.M)
CHILDREN_ROWS = ("linear_ops.cmd_children", "linear_ops.cmd_child_descriptions",
                 "linear_ops.cmd_children_json", "linear_ops.cmd_children_detail")
NAMED_ROWS = ("check_linear_identities.viewer_of", "plan_initiative_context.block",
              "harness.scenarios.lane_contract.board_states", "planner_score.collect",
              "groomer.read_population", "hand_work_migration.read_population",
              "groom_verify._card")
ALARM_ROW = "alarm-repair.yml#repair/Assemble the alarm's context"
#: The reads that decide or confirm a write, kept on Linear by name (DRE-9171).
WRITE_READ_CARD = "DRE-9171"
WRITE_READ_ROWS = ("linear_ops.get_issue", "linear_ops.landed",
                   "linear_ops._clobbered_terminal_state", "linear_ops.find_open",
                   "linear_ops.find_open_prefix")

QUERY = '"query($id: String!) { issue(id: $id) { id title } }"'
MUTATION = '"mutation($id: String!) { issueArchive(id: $id) { success } }"'

#: A throwaway `linear_ops`: the transport and its plumbing, in the real one's
#: shape — `_send` builds the `Request` with the key, `gql_paged` calls `gql`.
FAKE_OPS = '''
import urllib.request

API = "https://api.linear.app/graphql"


def api_key():
    return "lin_api_test"


def _send(payload):
    req = urllib.request.Request(API, data=payload,
                                 headers={"Authorization": api_key()})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def gql(query, variables=None):
    return _send(query.encode())


def gql_paged(query, variables=None):
    return [gql(query, variables)]


def get_issue(identifier):
    return gql("query($id: String!) { issue(id: $id) { id } }", {"id": identifier})
'''


# ── helpers ──────────────────────────────────────────────────────────────────


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


def site_names(root: Path) -> set[str]:
    return set(clr.scan(root))


def tree(tmp_path: Path, files: dict[str, str], with_ops: bool = True) -> Path:
    if with_ops:
        files = {"scripts/linear_ops.py": FAKE_OPS, **files}
    write(tmp_path, files)
    return tmp_path


def ledger_text(names, kept: dict | None = None) -> str:
    rows = [{"caller": n, "class": "move", "reason": clr.DEFAULT_REASON, "card": None}
            for n in sorted(names)]
    for row in rows:
        if row["caller"] in (kept or {}):
            row.update(kept[row["caller"]])
    return json.dumps({"schema": clr.SCHEMA, "reads": rows}, indent=2) + "\n"


def git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, env=env).stdout


def commit_all(root: Path, message: str = "change") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)


def init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    return root


def run_check(root: Path, base: str | None = None) -> tuple[int, str]:
    argv = [sys.executable, str(SCRIPT), "--root", str(root)]
    if base:
        argv += ["--base", base]
    done = subprocess.run(argv, capture_output=True, text=True, cwd=root)
    return done.returncode, done.stdout + done.stderr


def summary(out: str) -> tuple[int, ...]:
    m = SUMMARY.search(out)
    assert m, f"no summary line in:\n{out}"
    return tuple(int(x) for x in m.groups())


def listed_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """A throwaway repo on `main` whose ledger lists every site it holds."""
    root = init_repo(tmp_path / "repo")
    tree(root, files or {})
    (root / "config").mkdir(exist_ok=True)
    (root / clr.LEDGER).write_text(ledger_text(site_names(root)))
    commit_all(root, "base")
    return root


# ── this repo's own ledger ───────────────────────────────────────────────────


def _rows() -> dict[str, dict]:
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    assert data["schema"] == "linear-direct-reads/1"
    return {row["caller"]: row for row in data["reads"]}


def test_the_check_passes_on_this_branch_and_prints_its_summary():
    code, out = run_check(ROOT)
    assert code == 0, out
    found, listed, kept, pending, new, gone = summary(out)
    assert (new, gone) == (0, 0)
    assert found == listed and kept == 1 + len(WRITE_READ_ROWS)


def test_the_ledger_lists_every_site_the_scan_finds_and_nothing_else():
    sites = clr.scan(ROOT)
    assert set(_rows()) == set(sites)


def test_every_row_is_move_with_no_card_but_the_kept_ones():
    for caller, row in _rows().items():
        assert set(row) == {"caller", "class", "reason", "card"}, caller
        assert row["reason"].strip(), caller
        if caller == "plan_initiative_context.fetch":
            assert row == {"caller": caller, "class": "kept", "card": THIS_CARD,
                           "reason": clr.KEPT_FETCH_REASON}
        elif caller in WRITE_READ_ROWS:
            assert (row["class"], row["card"]) == ("kept", WRITE_READ_CARD), caller
        else:
            assert (row["class"], row["card"]) == ("move", None), caller


def test_a_writes_own_reads_are_kept_because_the_store_trails_linear():
    # DRE-9171's six reads, as the scan names them: `_card_look`'s read is
    # its inner `landed`. `_comment_look` sends nothing itself — it reads
    # through `_fetch_thread`, every thread read's site — so it has no row.
    sites = clr.scan(ROOT)
    rows = _rows()
    assert "linear_ops._comment_look" not in sites
    assert "linear_ops._card_look" not in sites
    for caller in WRITE_READ_ROWS:
        reason = rows[caller]["reason"]
        assert reason.startswith(clr.stays_reason(ROOT, sites[caller])), caller
        assert "store trails Linear" in reason, caller
        assert ("decides a write" in reason) != ("confirms a write" in reason), caller
        assert reason.endswith(" " + clr.NEW_CALLER_SENTENCE), caller
    assert rows["linear_ops._fetch_thread"]["class"] == "move"


def test_kept_fetch_reason_is_the_cards_words():
    assert clr.KEPT_FETCH_REASON == (
        "Downloads an approved design file from Linear's upload host with the "
        "pipeline's Linear key. The console's store holds no file bytes, so only "
        "Linear can serve it.")


def test_every_reason_opens_with_its_docstring_reason_or_the_default():
    sites = clr.scan(ROOT)
    stays = 0
    for caller, row in _rows().items():
        if caller == "plan_initiative_context.fetch":
            continue
        own = clr.stays_reason(ROOT, sites[caller])
        if own:
            stays += 1
            assert own.startswith(clr.STAYS_MARK)
        assert row["reason"].startswith(own or clr.DEFAULT_REASON), caller
    # get_issue, _clobbered_terminal_state, find_open, find_open_prefix,
    # viewer_id, the four children reads, and `landed` (the look
    # `_card_look` returns) carry the mark.
    assert stays == 10
    assert clr.DEFAULT_REASON == "Not yet moved to the door; part two of the switch-off decides."


def test_rows_other_code_calls_end_with_the_new_caller_sentence_and_no_other_row_does():
    sentence = "A new caller of this function is not a new read site."
    assert clr.NEW_CALLER_SENTENCE == sentence
    for caller, row in _rows().items():
        if caller.startswith("linear_ops.") or caller == "hygiene.linear":
            assert row["reason"].endswith(" " + sentence), caller
            assert row["reason"].count(sentence) == 1, caller
        else:
            assert sentence not in row["reason"], caller
    assert "hygiene.linear" in _rows()


def test_a_linear_ops_row_without_a_docstring_reason_is_the_default_then_the_sentence():
    assert _rows()["linear_ops.comment_count"]["reason"] == (
        f"{clr.DEFAULT_REASON} {clr.NEW_CALLER_SENTENCE}")


def test_the_ledger_names_the_shapes_the_card_names():
    rows = _rows()
    for caller in NAMED_ROWS + ("plan_initiative_context.fetch",):
        assert caller in rows, caller


def test_the_children_rows_say_the_store_holds_them_and_the_door_names_them_by_internal_id():
    for caller in CHILDREN_ROWS:
        reason = _rows()[caller]["reason"]
        assert "store holds a card's children" in reason, caller
        assert "the door names them by internal id" in reason, caller


def test_the_ledger_has_exactly_one_workflow_row():
    assert [c for c in _rows() if "#" in c] == [ALARM_ROW]


def test_this_repos_workflows_hold_exactly_one_read_site():
    sites = clr.scan_workflows(ROOT)
    assert [(s.name, s.path, s.line) for s in sites] == [
        (ALARM_ROW, ".github/workflows/alarm-repair.yml", 302)]


# ── transport 1: a call of gql / gql_paged, whatever it is read off ──────────


def test_a_query_through_linear_ops_gql_is_a_site_and_a_mutation_is_not(tmp_path):
    root = tree(tmp_path, {
        "scripts/reader.py": f"""
            import linear_ops

            def look(card):
                return linear_ops.gql({QUERY}, {{"id": card}})

            def archive(card):
                return linear_ops.gql({MUTATION}, {{"id": card}})

            def every_card():
                return linear_ops.gql_paged("{{ issues {{ nodes {{ id }} }} }}")
        """})
    names = site_names(root)
    assert {"reader.look", "reader.every_card"} <= names
    assert "reader.archive" not in names


def test_gql_or_linear_ops_gql_is_a_site(tmp_path):
    root = tree(tmp_path, {
        "scripts/picker.py": f"""
            import linear_ops

            def block(card, *, gql=None):
                return (gql or linear_ops.gql)({QUERY}, {{"id": card}})

            def chosen(card, gql=None):
                return (gql if gql else linear_ops.gql)({QUERY}, {{"id": card}})

            def block_write(card, *, gql=None):
                return (gql or linear_ops.gql)({MUTATION}, {{"id": card}})

            def chosen_write(card, gql=None):
                return (gql if gql else linear_ops.gql)({MUTATION}, {{"id": card}})
        """})
    names = site_names(root)
    assert {"picker.block", "picker.chosen"} <= names
    assert not {"picker.block_write", "picker.chosen_write"} & names


SPELLINGS = {
    "scripts/spellings.py": """
        import linear_ops

        def by_param(lops, card):
            return lops.gql({doc}, {{"id": card}})

        def by_ops(ops):
            return ops.gql_paged({doc})

        class Replacements:
            def __init__(self, lops):
                self.lops = lops

            def _card(self, card):
                return self.lops.gql({doc}, {{"id": card}})
    """,
    "scripts/aliased.py": """
        import linear_ops as lops

        def collect(card):
            return lops.gql({doc}, {{"id": card}})
    """,
}


def _spellings(doc: str) -> dict[str, str]:
    return {k: v.replace("{doc}", doc).replace("{{", "{").replace("}}", "}")
            for k, v in SPELLINGS.items()}


def test_the_module_under_any_other_name_is_a_transport(tmp_path):
    root = listed_repo(tmp_path)
    write(root, _spellings(QUERY))
    code, out = run_check(root)
    assert code == 1, out
    for name, rel in (("spellings.by_param", "scripts/spellings.py"),
                      ("spellings.by_ops", "scripts/spellings.py"),
                      ("spellings._card", "scripts/spellings.py"),
                      ("aliased.collect", "scripts/aliased.py")):
        assert re.search(rf"^linear-reads-check: new {re.escape(name)} — {rel}:\d+$", out, re.M), out
    assert "Replacements" not in out


def test_the_same_spellings_sending_a_mutation_are_not_sites(tmp_path):
    root = tree(tmp_path, _spellings(MUTATION))
    names = site_names(root)
    assert not {n for n in names if n.split(".")[0] in ("spellings", "aliased")}


def test_a_document_the_check_cannot_resolve_counts_as_a_read(tmp_path):
    root = tree(tmp_path, {
        "scripts/handed.py": f"""
            import linear_ops

            def send(doc, variables=None):
                return linear_ops.gql(doc, variables)

            def built(n):
                return linear_ops.gql(make_query(n))

            def placeholder(op):
                return linear_ops.gql(f"{{op}} {{{{ viewer {{{{ id }}}} }}}}")

            WRITE = {MUTATION}
            READ = {QUERY}

            def constant_write(card):
                return linear_ops.gql(WRITE, {{"id": card}})

            def constant_read(card):
                return linear_ops.gql(READ, {{"id": card}})

            def local_write(card):
                doc = {MUTATION}
                return linear_ops.gql(doc, {{"id": card}})
        """})
    names = site_names(root)
    assert {"handed.send", "handed.built", "handed.placeholder", "handed.constant_read"} <= names
    assert not {"handed.constant_write", "handed.local_write"} & names


#: Every spelling the critic sent on review of #1494 that the first scanner
#: missed, plus their neighbors. `{doc}` is the document each one sends.
RESOLVED_SPELLINGS = """
    import functools
    import subprocess
    from functools import partial
    from urllib.request import urlopen

    import linear_ops
    import linear_ops as lo
    from linear_ops import gql as q
    from linear_ops import gql_paged as pages

    SEND = linear_ops.gql
    HOST = "api.linear" + ".app"
    URL = "https://" + HOST + "/graphql"

    def aliased_import(card):
        return q({doc}, {{"id": card}})

    def aliased_paged():
        return pages({doc})

    def assigned_alias(card):
        run = linear_ops.gql
        return run({doc}, {{"id": card}})

    def module_alias(card):
        return SEND({doc}, {{"id": card}})

    def default_alias(card, *, send=lo.gql_paged):
        return send({doc}, {{"id": card}})

    def partial_call():
        return functools.partial(linear_ops.gql, {doc})()

    def partial_bound(card):
        ask = partial(lo.gql)
        return ask({doc}, {{"id": card}})

    def getattr_call(card):
        return getattr(linear_ops, "gql")({doc}, {{"id": card}})

    def subscript_call(card):
        return vars(linear_ops)["gql_paged"]({doc}, {{"id": card}})
"""


def _resolved(doc: str) -> dict[str, str]:
    return {"scripts/resolved.py": RESOLVED_SPELLINGS.replace("{doc}", doc)
            .replace("{{", "{").replace("}}", "}")}


RESOLVED_SITES = {f"resolved.{n}" for n in (
    "aliased_import", "aliased_paged", "assigned_alias", "module_alias",
    "default_alias", "partial_call", "partial_bound", "getattr_call",
    "subscript_call")}


def test_an_aliased_assigned_partial_or_getattr_transport_is_a_site(tmp_path):
    names = site_names(tree(tmp_path, _resolved(QUERY)))
    assert RESOLVED_SITES <= names, sorted(RESOLVED_SITES - names)


def test_the_same_resolved_spellings_sending_a_mutation_are_not_sites(tmp_path):
    names = site_names(tree(tmp_path, _resolved(MUTATION)))
    assert not {n for n in names if n.startswith("resolved.")}, sorted(names)


def test_an_aliased_import_is_refused_as_new_by_the_check(tmp_path):
    root = listed_repo(tmp_path)
    write(root, {"scripts/a.py": f"""
        from linear_ops import gql as q

        def peek():
            return q({QUERY})
    """})
    code, out = run_check(root)
    assert code == 1, out
    assert re.search(r"^linear-reads-check: new a\.peek — scripts/a\.py:5$", out, re.M), out


def test_a_getattr_off_linear_ops_by_a_name_the_check_cannot_read_is_a_site(tmp_path):
    root = tree(tmp_path, {"scripts/dynamic.py": """
        import linear_ops

        def by_name(which, doc):
            return getattr(linear_ops, which)(doc)

        def door(lops):
            via_door = getattr(lops, "card_via_door", None)
            return via_door("DRE-1") if via_door else None
    """})
    names = site_names(root)
    assert "dynamic.by_name" in names
    assert "dynamic.door" not in names


def test_anything_aimed_at_linears_host_is_a_site_however_it_is_sent(tmp_path):
    root = tree(tmp_path, {"scripts/raw.py": """
        import os
        import subprocess
        from urllib.request import urlopen

        import linear_ops

        HOST = "api.linear" + ".app"
        URL = "https://api.linear.app/graphql"
        _LINEAR_API_HOST = "api.linear.app"

        def curl_literal():
            return subprocess.run(["curl", "-s", "https://api.linear.app/graphql",
                                   "-d", '{"query":"{ viewer { id } }"}'])

        def curl_constant():
            return subprocess.run(["curl", "-s", URL], check=True)

        def curl_linear_ops_api():
            return subprocess.check_output(["curl", "-s", linear_ops.API])

        def shell():
            return os.system("curl -s https://api.linear.app/graphql")

        def built_host():
            return urlopen("https://" + HOST + "/graphql")

        def built_locally():
            host = "api.linear" + ".app"
            return urlopen(f"https://{host}/graphql")

        def other_client(session):
            return session.post("https://api.linear.app/graphql", json={})

        def classify(line):
            '''A log line naming api.linear.app is a Linear fault.'''
            return _LINEAR_API_HOST in line

        def elsewhere():
            return subprocess.run(["curl", "-s", "https://example.com/graphql"])
    """})
    names = site_names(root)
    expected = {f"raw.{n}" for n in (
        "curl_literal", "curl_constant", "curl_linear_ops_api", "shell",
        "built_host", "built_locally", "other_client")}
    assert expected <= names, sorted(expected - names)
    assert not {"raw.classify", "raw.elsewhere", "raw.<module>"} & names, sorted(names)
    assert not {"linear_ops.<module>", "linear_ops._send"} & names


def test_a_function_patched_in_as_gql_is_plumbing_and_a_closure_over_gql_is_a_site(tmp_path):
    root = tree(tmp_path, {"scripts/seam.py": f"""
        import linear_ops

        def counting():
            real = linear_ops.gql

            def counted(query, variables=None):
                return real(query, variables)

            linear_ops.gql = counted
            try:
                return linear_ops.gql({QUERY})
            finally:
                linear_ops.gql = real

        def forwarding():
            real = linear_ops.gql

            def forward(query):
                return real(query)
            return forward
    """})
    names = site_names(root)
    assert {"seam.counting", "seam.forward"} <= names
    assert not {"seam.counted", "seam.forwarding"} & names


def test_a_message_naming_the_endpoint_by_its_constant_is_not_a_site_and_a_literal_is(tmp_path):
    root = tree(tmp_path, {
        "scripts/linear_ops.py": FAKE_OPS + '''

def _refusal():
    return RuntimeError(f"linear error from {API}: refused, no request sent")
''',
        "scripts/loud.py": """
            def complain():
                raise RuntimeError("could not reach https://api.linear.app/graphql")
        """})
    names = site_names(root)
    assert "linear_ops._refusal" not in names
    assert "loud.complain" in names


def test_an_inner_function_is_named_by_its_own_name(tmp_path):
    root = tree(tmp_path, {
        "scripts/hygiene.py": """
            def read_only_linear(gql):
                def linear(query, variables=None):
                    return gql(query, variables)
                return linear
        """})
    names = site_names(root)
    assert "hygiene.linear" in names
    assert "hygiene.read_only_linear" not in names


# ── transports 2 and 3: a request aimed at Linear, or carrying its key ───────


def test_a_request_to_linear_ops_api_or_the_host_is_a_site(tmp_path):
    root = tree(tmp_path, {
        "scripts/identities.py": """
            import json
            import urllib.request
            from urllib.request import Request, urlopen

            import linear_ops

            VIEWER_QUERY = "{ viewer { id name admin } }"

            def viewer_of(key):
                payload = json.dumps({"query": VIEWER_QUERY}).encode()
                req = urllib.request.Request(linear_ops.API, data=payload,
                                             headers={"Authorization": key})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read())

            def by_host(key):
                req = Request("https://api.linear.app/graphql", data=b"{}",
                              headers={"Authorization": key})
                return urlopen(req)

            def elsewhere(key):
                req = urllib.request.Request("https://example.com/graphql", data=b"{}",
                                             headers={"Authorization": key})
                return urllib.request.urlopen(req)
        """})
    names = site_names(root)
    assert {"identities.viewer_of", "identities.by_host"} <= names
    assert "identities.elsewhere" not in names
    assert not {"linear_ops.gql", "linear_ops.gql_paged", "linear_ops._send"} & names


def test_a_request_carrying_the_linear_key_is_a_site_whatever_its_url(tmp_path):
    root = tree(tmp_path, {
        "scripts/designs.py": """
            import urllib.request

            import linear_ops

            def fetch_inline(url):
                request = urllib.request.Request(
                    url, headers={"Authorization": linear_ops.api_key()})
                with urllib.request.urlopen(request, timeout=60) as resp:
                    return resp.read()

            def fetch(url, dest, *, opener=None):
                key = linear_ops.api_key()
                request = urllib.request.Request(url, headers={"Authorization": key})
                opener = opener or urllib.request.build_opener().open
                with opener(request, timeout=60) as resp:
                    return resp.read()

            def keyless(url):
                request = urllib.request.Request(url)
                with urllib.request.urlopen(request) as resp:
                    return resp.read()
        """})
    names = site_names(root)
    assert {"designs.fetch_inline", "designs.fetch"} <= names
    assert "designs.keyless" not in names
    assert "linear_ops._send" not in names


# ── a new caller of a listed site ────────────────────────────────────────────


def test_a_new_caller_of_a_listed_site_is_not_a_finding(tmp_path):
    root = listed_repo(tmp_path, {
        "scripts/hygiene.py": """
            def read_only_linear(gql):
                def linear(query, variables=None):
                    return gql(query, variables)
                return linear
        """})
    assert {"linear_ops.get_issue", "hygiene.linear"} <= site_names(root)
    write(root, {
        "scripts/new_reader.py": """
            import linear_ops

            def title(card):
                return linear_ops.get_issue(card)["title"]
        """,
        "scripts/hygiene_new_leg.py": """
            def check(ctx):
                return ctx.linear("query { issues { nodes { id } } }")
        """})
    code, out = run_check(root)
    assert code == 0, out
    assert "linear-reads-check: new" not in out


# ── which files the scan walks ───────────────────────────────────────────────


def test_the_scan_walks_scripts_recursively_and_names_a_nested_file_by_its_path(tmp_path):
    board = """
        def board_states(gql):
            return gql("query { workflowStates { nodes { id name } } }")
    """
    root = tree(tmp_path, {
        "scripts/lane_contract.py": board,
        "scripts/harness/scenarios/lane_contract.py": board,
        # Never read: neither parses, so reading one would raise.
        "scripts/__pycache__/stale.py": "def board_states(gql:\n",
        "scripts/harness/__pycache__/stale.py": "def board_states(gql:\n",
        "tests/test_reader.py": "def board_states(gql:\n",
    }, with_ops=False)
    assert site_names(root) == {"lane_contract.board_states",
                                "harness.scenarios.lane_contract.board_states"}


def test_a_nested_site_name_can_never_equal_a_top_level_one():
    assert clr.module_name(Path("lane_contract.py")) == "lane_contract"
    assert clr.module_name(Path("harness/scenarios/lane_contract.py")) == \
        "harness.scenarios.lane_contract"


# ── transport 6: a workflow step ─────────────────────────────────────────────

WORKFLOW = """
name: Throwaway
on: push
jobs:
  sweep:
    runs-on: ubuntu-latest
    steps:
      - name: Ask Linear by hand
        run: |
          curl -s https://api.linear.app/graphql -d '{"query":"{ viewer { id } }"}'
      - name: Inline read
        run: |
          python3 - <<'PY'
          import linear_ops
          print(linear_ops.gql("query { viewer { id } }"))
          PY
      - name: Inline write
        run: |
          python3 - <<'PY'
          import linear_ops
          linear_ops.gql("mutation { issueArchive(id: \\"x\\") { success } }")
          PY
      - name: Run a script
        run: python3 scripts/linear_ops.py get DRE-1
"""


def test_a_workflow_run_block_that_talks_to_linear_is_a_site(tmp_path):
    write(tmp_path, {".github/workflows/throwaway.yml": WORKFLOW})
    sites = {s.name: s for s in clr.scan_workflows(tmp_path)}
    assert set(sites) == {"throwaway.yml#sweep/Ask Linear by hand",
                          "throwaway.yml#sweep/Inline read"}
    assert sites["throwaway.yml#sweep/Ask Linear by hand"].path == \
        ".github/workflows/throwaway.yml"
    assert sites["throwaway.yml#sweep/Ask Linear by hand"].line == 10


INLINE_WORKFLOW = """
name: Inline
on: push
env:
  LINEAR_GRAPHQL: https://api.linear.app/graphql
jobs:
  sweep:
    runs-on: ubuntu-latest
    env:
      ENDPOINT: https://api.linear.app/graphql
    steps:
      - name: Script step
        uses: actions/github-script@v7
        with:
          script: |
            const r = await fetch("https://api.linear.app/graphql", {method: "POST"});
      - name: Env-held URL
        env:
          URL: https://api.linear.app/graphql
        run: |
          curl -s "$URL" -d '{"query":"{ viewer { id } }"}'
      - name: Elsewhere
        uses: actions/github-script@v7
        with:
          script: |
            const r = await fetch("https://example.com/graphql");
"""


def test_a_workflow_step_talks_to_linear_through_with_or_env_too(tmp_path):
    write(tmp_path, {".github/workflows/inline.yml": INLINE_WORKFLOW})
    sites = {s.name: s for s in clr.scan_workflows(tmp_path)}
    assert set(sites) == {"inline.yml#env", "inline.yml#sweep/env",
                          "inline.yml#sweep/Script step",
                          "inline.yml#sweep/Env-held URL"}
    assert sites["inline.yml#sweep/Script step"].line == 16
    assert sites["inline.yml#sweep/Env-held URL"].line == 19
    assert sites["inline.yml#env"].line == 5
    assert sites["inline.yml#sweep/env"].line == 10


# ── the four ledger rules, over throwaway git repositories ───────────────────

READER = f"""
    import linear_ops

    def look(card):
        return linear_ops.gql({QUERY}, {{"id": card}})
"""


def feature(root: Path) -> None:
    git(root, "checkout", "-q", "-b", "feature")


def set_ledger(root: Path, names, kept=None) -> None:
    (root / clr.LEDGER).write_text(ledger_text(names, kept))


def test_a_row_added_beside_its_new_site_fails_as_new(tmp_path):
    root = listed_repo(tmp_path)
    feature(root)
    write(root, {"scripts/reader.py": READER})
    set_ledger(root, site_names(root))
    commit_all(root)
    code, out = run_check(root, "main")
    assert code == 1, out
    assert re.search(r"^linear-reads-check: new reader\.look — scripts/reader\.py:5$", out, re.M)


def test_a_row_alone_is_pending_and_the_site_lands_on_a_base_that_holds_it(tmp_path):
    root = listed_repo(tmp_path)
    feature(root)
    set_ledger(root, site_names(root) | {"reader.look"})
    commit_all(root, "the row alone")
    code, out = run_check(root, "main")
    assert code == 0, out
    assert "linear-reads-check: pending reader.look" in out.splitlines()
    assert summary(out)[3] == 1
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "feature")
    git(root, "checkout", "-q", "-b", "the-site")
    write(root, {"scripts/reader.py": READER})
    commit_all(root, "the site")
    code, out = run_check(root, "main")
    assert code == 0, out
    assert summary(out)[3:] == (0, 0, 0)


def test_a_rename_in_one_change_fails_as_new(tmp_path):
    root = listed_repo(tmp_path, {"scripts/reader.py": READER})
    feature(root)
    write(root, {"scripts/reader.py": READER.replace("def look", "def peek")})
    names = (site_names(root) - {"reader.look"}) | {"reader.peek"}
    set_ledger(root, names)
    commit_all(root, "rename")
    code, out = run_check(root, "main")
    assert code == 1, out
    assert re.search(r"^linear-reads-check: new reader\.peek — ", out, re.M)


def test_deleting_a_site_with_its_row_passes_and_without_it_fails_as_gone(tmp_path):
    root = listed_repo(tmp_path, {"scripts/reader.py": READER})
    feature(root)
    (root / "scripts" / "reader.py").unlink()
    commit_all(root, "delete the site, keep the row")
    code, out = run_check(root, "main")
    assert code == 1, out
    assert "linear-reads-check: gone reader.look" in out.splitlines()
    set_ledger(root, site_names(root))
    commit_all(root, "and its row")
    code, out = run_check(root, "main")
    assert code == 0, out
    assert summary(out)[5] == 0


def test_removing_the_row_of_a_site_that_stays_fails_as_new(tmp_path):
    root = listed_repo(tmp_path, {"scripts/reader.py": READER})
    feature(root)
    set_ledger(root, site_names(root) - {"reader.look"})
    commit_all(root, "drop the row")
    code, out = run_check(root, "main")
    assert code == 1, out
    assert re.search(r"^linear-reads-check: new reader\.look — scripts/reader\.py:5$", out, re.M)


def test_no_ledger_at_the_merge_base_is_judged_against_the_heads(tmp_path):
    root = init_repo(tmp_path / "repo")
    tree(root, {"scripts/reader.py": READER})
    commit_all(root, "before the ledger")
    base = git(root, "rev-parse", "HEAD").strip()
    feature(root)
    (root / "config").mkdir()
    set_ledger(root, site_names(root))
    commit_all(root, "the ledger")
    code, out = run_check(root, "main")
    assert code == 0, out
    assert (f"linear-reads-check: no ledger at the merge base {base[:7]} — judged "
            "against the head's ledger") in out.splitlines()


def test_a_head_with_no_ledger_fails(tmp_path):
    root = listed_repo(tmp_path)
    feature(root)
    (root / clr.LEDGER).unlink()
    commit_all(root, "drop the ledger")
    code, out = run_check(root, "main")
    assert code == 1, out
    assert summary(out)


@pytest.mark.parametrize("row", [
    {"class": "kept", "card": None, "reason": "The store cannot serve it."},
    {"class": "kept", "card": "DRE-1", "reason": ""},
    {"class": "kept", "card": "DRE-1", "reason": "   "},
])
def test_a_kept_row_without_a_card_or_a_reason_fails(tmp_path, row):
    root = listed_repo(tmp_path)
    feature(root)
    set_ledger(root, site_names(root), kept={"linear_ops.get_issue": row})
    commit_all(root, "keep it")
    code, out = run_check(root, "main")
    assert code == 1, out
    assert "linear-reads-check: invalid linear_ops.get_issue" in out


def test_a_well_formed_kept_row_passes(tmp_path):
    root = listed_repo(tmp_path)
    feature(root)
    set_ledger(root, site_names(root), kept={"linear_ops.get_issue": {
        "class": "kept", "card": "DRE-1", "reason": "Decides a write."}})
    commit_all(root, "keep it")
    code, out = run_check(root, "main")
    assert code == 0, out
    assert summary(out)[2] == 1


def test_with_no_base_the_merge_base_is_head(tmp_path):
    root = listed_repo(tmp_path)
    code, out = run_check(root)
    assert code == 0, out
    assert "no ledger at the merge base" not in out


# ── a throwaway copy of this repo's scripts ──────────────────────────────────


@pytest.fixture(scope="module")
def real_copy(tmp_path_factory):
    root = init_repo(tmp_path_factory.mktemp("real") / "repo")
    shutil.copytree(ROOT / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / ".github" / "workflows", root / ".github" / "workflows")
    (root / "config").mkdir()
    shutil.copy(LEDGER, root / clr.LEDGER)
    commit_all(root, "a copy of this repo's reads")
    return root


@pytest.fixture
def scripts_copy(real_copy, tmp_path):
    root = tmp_path / "copy"
    shutil.copytree(real_copy, root)
    return root


@pytest.mark.parametrize("doc,expected", [(QUERY, 1), (MUTATION, 0)])
def test_a_new_function_in_a_copy_of_the_scripts(scripts_copy, doc, expected):
    write(scripts_copy, {"scripts/brand_new_reader.py": f"""
        import linear_ops

        def peek(card):
            return linear_ops.gql({doc}, {{"id": card}})
    """})
    code, out = run_check(scripts_copy)
    assert code == expected, out
    new = [l for l in out.splitlines() if l.startswith("linear-reads-check: new ")]
    if expected:
        assert new == ["linear-reads-check: new brand_new_reader.peek — "
                       "scripts/brand_new_reader.py:5"]
    else:
        assert new == []


def test_a_new_caller_in_a_copy_of_the_scripts_is_not_a_finding(scripts_copy):
    write(scripts_copy, {
        "scripts/brand_new_caller.py": """
            import linear_ops

            def title(card):
                return linear_ops.get_issue(card)["title"]
        """,
        "scripts/hygiene_brand_new.py": """
            def leg(ctx):
                return ctx.linear("query { issues { nodes { id } } }")
        """})
    code, out = run_check(scripts_copy)
    assert code == 0, out
    assert "linear-reads-check: new" not in out


# ── the CI job ───────────────────────────────────────────────────────────────


def _job() -> dict:
    jobs = yaml.safe_load(TESTS_YML.read_text(encoding="utf-8"))["jobs"]
    assert "linear-reads-check" in jobs
    return jobs["linear-reads-check"]


def test_the_job_is_named_unnarrowed_and_carries_no_job_level_if():
    job = _job()
    assert job["name"] == "linear reads check"
    assert "if" not in job
    assert "continue-on-error" not in job
    for step in job["steps"]:
        assert "if" not in step and "continue-on-error" not in step, step


def test_the_job_checks_out_full_history():
    checkout = [s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkout) == 1
    assert checkout[0]["with"]["fetch-depth"] == 0


def test_the_job_passes_base_only_on_a_pull_request_through_env():
    runs = [s for s in _job()["steps"] if "check_linear_reads.py" in str(s.get("run", ""))]
    assert len(runs) == 1
    step = runs[0]
    assert step["env"]["BASE_REF"] == "${{ github.base_ref }}"
    run = step["run"]
    assert "${{" not in run
    assert re.search(r'if \[ -n "\$BASE_REF" \]', run)
    assert '--base "origin/$BASE_REF"' in run
    assert re.search(r"^\s*python3 scripts/check_linear_reads\.py\s*$", run, re.M)


def test_the_jobs_comment_says_the_gate_the_runs_that_refuse_nothing_and_the_scope():
    text = TESTS_YML.read_text(encoding="utf-8")
    at = text.index("  linear-reads-check:")
    comment = []
    for line in reversed(text[:at].splitlines()):
        if not line.strip().startswith("#"):
            break
        comment.append(line.strip().lstrip("#").strip())
    said = " ".join(reversed(comment))
    assert "pull-request run is the gate" in said
    assert "refuse nothing" in said
    assert "this repo alone" in said


# ── proof reach and the docstring ────────────────────────────────────────────


def test_proof_reach_declares_the_tag():
    lines = json.loads(PROOF_REACH.read_text(encoding="utf-8"))["log_lines"]
    rows = [r for r in lines if r["tag"] == "linear-reads-check:"]
    assert len(rows) == 1
    assert rows[0]["printed_by"] == "scripts/check_linear_reads.py"
    assert rows[0]["constant"] == "TAG"
    assert clr.TAG == "linear-reads-check:"


def test_the_module_docstring_says_what_the_card_asks():
    doc = " ".join(clr.__doc__.split())
    for phrase in (
        "innermost function or method",
        "`groom_verify._card`",
        "`hygiene.linear`",
        "`lops`", "`ops`", "`self.lops`",
        "`plan_initiative_context.fetch`",
        "`api_key()`",
        "plumbing is not a site",
        "`<workflow file>#<job id>/<step name>`",
        "`__pycache__` skipped",
        "`harness.scenarios.lane_contract.board_states`",
        "SCOPE: THIS REPO ALONE",
        "agent-bureau's scripts and workflows are not scanned",
        "A NEW CALLER OF A LISTED SITE IS NOT A FINDING",
        "A Linear write is allowed",
        "THE LEDGER IS READ AT THE MERGE BASE",
        "Listed.", "Pending.", "Gone.", "No ledger at the merge base",
    ):
        assert phrase in doc, phrase
