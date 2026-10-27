"""RED-first: one redaction seam turns every roster form into its role, one guard refuses added text that still carries one (DRE-8166).

`scripts/name_redact.py` is the single point where text leaves a run. It reads the live roster
(`scripts/name_roster.py`, DRE-8165) and these tests hold:

  A. `redact` turns every form of the admin into `[the CEO]` and every form of anyone else into `[a person]`,
     longest form first, a login in backticks and an email included; text with no form comes back
     byte-identical, and a role it wrote is never matched again.
  B. `findings` reports the file, the one-based line and the kind, never the matched text.
  C. `added_lines` over a throwaway git repository reads only the branch's own added lines by new-file line
     number, plus its non-merge commit bodies: a default branch merged in, carrying a name, yields nothing.
  D. The `guard` CLI over its three readers — a text file, a git range and git's pre-push stdin — exits 1
     on a remaining form and 2 on an unreadable roster when `NAME_GUARD_LIVE` is `true`, prints a
     `name-guard: refused — …` line with no name in it, and when not live prints `dry` and exits 0.
  E. `self-test` prints its `ok` line and never the line it built; `guard --pre-push` prints it first.
  F. The `redact` CLI prints or rewrites; an unreadable roster exits 2 live and leaves the text whole dry.
  G. `config/proof-reach.json` keeps the `name-guard:` line.

Every name below is synthetic and carries the marker `synth`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_name_redact.py -v
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import name_redact  # noqa: E402
import name_roster  # noqa: E402
import proof_reach  # noqa: E402

MARKER = "synth"
OWNER = "synth-org"

#: The admin, as Linear spells them, and their GitHub member.
CEO = {"name": "Synthora Synthbridge", "displayName": "Synthora",
       "email": "synthora.synthbridge@synthetic.invalid", "admin": True, "active": True}
CEO_LOGIN = "synthbridge-synth"

#: A person who is not the admin, and their GitHub member.
PERSON = {"name": "Synthia Synthwood", "displayName": "synthia",
          "email": "synthia.synthwood@synthetic.invalid", "admin": False, "active": True}
PERSON_LOGIN = "synthwood-synth"

#: A person whose surname is the word in the person role: a written role must never match again.
ROLE_WORD = {"name": "Synthel Person", "displayName": "synthel",
             "email": "synthel@synthetic.invalid", "admin": False, "active": True}

#: A login the caller hands over with `--actor` (a workflow run's actor), not a member.
ACTOR_LOGIN = "synthactor"

PROFILES = {CEO_LOGIN: CEO["name"], PERSON_LOGIN: PERSON["name"], ACTOR_LOGIN: "Synthactor Synthmere"}

#: Every synthetic string that must never be printed.
SECRETS = (CEO["name"], "Synthora", "Synthbridge", CEO["email"], CEO_LOGIN,
           PERSON["name"], "synthia", "Synthwood", PERSON["email"], PERSON_LOGIN,
           ACTOR_LOGIN, "Synthmere", "Synthel")

LIVE = {"NAME_GUARD_LIVE": "true"}
DRY = {"NAME_GUARD_LIVE": "false"}


def _gql(users):
    def gql(query, variables=None):
        return {"users": {"nodes": list(users), "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    return gql


def _gh(members, profiles):
    def gh(path):
        if path.startswith(f"orgs/{OWNER}/members"):
            return [{"login": m} for m in members] if path.endswith("&page=1") else []
        if path.startswith("users/"):
            return {"name": profiles.get(path[len("users/"):])}
        raise AssertionError("unexpected gh path")
    return gh


def reader(users=(CEO, PERSON), members=(CEO_LOGIN, PERSON_LOGIN), seen=None):
    """`name_roster.read` over synthetic Linear users and GitHub members, as the CLI calls it."""
    def read(**kw):
        if seen is not None:
            seen.append(kw)
        return name_roster.read(gql=_gql(users), gh=_gh(members, PROFILES), **kw)
    return read


def unreadable(reason="linear users"):
    def read(**kw):
        raise name_roster.RosterUnreadable(reason)
    return read


def roster(**kw) -> name_roster.Roster:
    return reader(**kw)(owner=OWNER, extra_logins=())


def no_secret(text: str):
    low = text.casefold()
    for s in SECRETS:
        assert s.casefold() not in low, "a synthetic name reached the output"
    assert MARKER not in low.replace(OWNER, "").replace("synthetic", ""), \
        "a synthetic string reached the output"


# --------------------------------------------------------------------------- #
# A. redact                                                                    #
# --------------------------------------------------------------------------- #

def test_the_contract_constants():
    assert name_redact.SWITCH == "NAME_GUARD_LIVE"
    assert name_redact.TAG == "name-guard:"
    assert name_redact.Finding._fields == ("file", "line", "kind")


CEO_CASES = [
    ("Synthora Synthbridge wrote it", "[the CEO] wrote it"),
    ("Synthora  Synthbridge\twrote it", "[the CEO]\twrote it"),
    ("asked SYNTHBRIDGE today", "asked [the CEO] today"),
    ("Synthora said so", "[the CEO] said so"),
    ("run by `synthbridge-synth` at noon", "run by `[the CEO]` at noon"),
    ("mail synthora.synthbridge@synthetic.invalid now", "mail [the CEO] now"),
    ("(Synthbridge), and Synthora Synthbridge.", "([the CEO]), and [the CEO]."),
]

PERSON_CASES = [
    ("Synthia Synthwood wrote it", "[a person] wrote it"),
    ("synthia Synthwood wrote it", "[a person] wrote it"),
    ("by Synthwood", "by [a person]"),
    ("ping synthia", "ping [a person]"),
    ("actor `synthwood-synth`", "actor `[a person]`"),
    ("<synthia.synthwood@synthetic.invalid>", "<[a person]>"),
]


@pytest.mark.parametrize("text,want", CEO_CASES)
def test_every_form_of_the_admin_becomes_the_ceo(text, want):
    assert name_redact.redact(text, roster()) == want


@pytest.mark.parametrize("text,want", PERSON_CASES)
def test_every_form_of_anyone_else_becomes_a_person(text, want):
    assert name_redact.redact(text, roster()) == want


def test_the_longest_form_wins_so_a_full_name_is_never_half_replaced():
    r = roster()
    out = name_redact.redact("Synthora Synthbridge and synthia.synthwood@synthetic.invalid", r)
    assert out == "[the CEO] and [a person]"
    assert "Synthora" not in out and "@" not in out


def test_text_with_no_form_comes_back_byte_identical():
    r = roster()
    for text in ("", "nothing to see\r\n  here \t\n", "a synthesis of Synthwoodshed — ü x\n",
                 "the CEO and [a person] and [the CEO]"):
        assert name_redact.redact(text, r) == text


def test_a_written_role_is_never_matched_again():
    r = roster(users=(CEO, PERSON, ROLE_WORD))
    once = name_redact.redact("Synthel Person met Synthwood, then person", r)
    assert once == "[a person] met [a person], then [a person]"
    assert name_redact.redact(once, r) == once
    assert name_redact.findings([("f.md", 1, once)], r) == []


def test_only_whole_words_are_replaced():
    r = roster()
    assert name_redact.redact("the Synthwoodshed and asynthia", r) == "the Synthwoodshed and asynthia"


# --------------------------------------------------------------------------- #
# B. findings                                                                  #
# --------------------------------------------------------------------------- #

def test_findings_name_the_file_line_and_kind_and_never_the_text():
    r = roster()
    lines = [("a.md", 1, "clean"), ("a.md", 2, "by Synthia Synthwood"),
             ("b.md", 7, "`synthbridge-synth` and synthia.synthwood@synthetic.invalid"),
             ("b.md", 9, "[a person] only")]
    found = name_redact.findings(lines, r)
    assert found == [name_redact.Finding("a.md", 2, "full-name"),
                     name_redact.Finding("b.md", 7, "login"),
                     name_redact.Finding("b.md", 7, "email")]
    for f in found:
        no_secret(repr(f) + str(f))


def test_findings_count_the_longest_form_only():
    r = roster()
    assert name_redact.findings([("a.md", 3, "Synthora Synthbridge")], r) == [
        name_redact.Finding("a.md", 3, "full-name")]


# --------------------------------------------------------------------------- #
# C. added_lines over a throwaway repository                                    #
# --------------------------------------------------------------------------- #

def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for key, value in (("user.email", "fixture@example.invalid"), ("user.name", "fixture"),
                       ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        git(repo, "config", key, value)
    commit(repo, {"notes.md": "one\ntwo\nthree\nfour\nfive\n"}, "base")
    return repo


def commit(repo: Path, files: dict, message: str) -> str:
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def branch_with_merge(tmp_path: Path, body: str = "written for Synthia Synthwood"):
    """A branch that changed two files, then merged in a default branch that gained a named file."""
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feature")
    first = commit(repo, {"notes.md": "one\ntwo\nTHREE\nfour\nfive\nsix\n"},
                   f"edit notes\n\n{body}")
    second = commit(repo, {"docs/new file.md": "alpha\n++ beta\n"}, "add a doc")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"proof.md": "decided by Synthora Synthbridge\n"}, "main gains a name\n\nby Synthwood")
    git(repo, "checkout", "-q", "feature")
    git(repo, "merge", "-q", "--no-edit", "-m", "Merge main, Synthora was here", "main")
    head = git(repo, "rev-parse", "HEAD")
    return repo, first, second, head


def test_added_lines_reads_only_the_branchs_own_lines_and_commit_bodies(tmp_path):
    repo, first, second, head = branch_with_merge(tmp_path)
    got = name_redact.added_lines("main", head, cwd=str(repo))
    files = [t for t in got if not t[0].startswith("commit:")]
    assert sorted(files) == sorted([("notes.md", 3, "THREE"), ("notes.md", 6, "six"),
                                    ("docs/new file.md", 1, "alpha"),
                                    ("docs/new file.md", 2, "++ beta")])
    bodies = [t for t in got if t[0].startswith("commit:")]
    assert (f"commit:{first[:7]}", 1, "edit notes") in bodies
    assert (f"commit:{first[:7]}", 3, "written for Synthia Synthwood") in bodies
    assert (f"commit:{second[:7]}", 1, "add a doc") in bodies
    assert {t[0] for t in bodies} == {f"commit:{first[:7]}", f"commit:{second[:7]}"}, \
        "neither the merge commit nor the default branch's commit is read"
    assert not [t for t in got if "proof.md" in t[0] or "decided by" in t[2]]


def test_a_merged_in_default_branch_carrying_a_name_yields_no_finding(tmp_path):
    repo, _, _, head = branch_with_merge(tmp_path, body="written for [a person]")
    assert "Synthora" in git(repo, "show", "HEAD:proof.md")
    lines = name_redact.added_lines("main", head, cwd=str(repo))
    assert lines
    assert name_redact.findings(lines, roster()) == []


def test_added_lines_raises_on_a_range_git_cannot_read(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(name_redact.InputUnreadable) as exc:
        name_redact.added_lines("main", "no-such-ref", cwd=str(repo))
    assert "no-such-ref" not in str(exc.value)


# --------------------------------------------------------------------------- #
# D. the guard CLI over its three readers                                       #
# --------------------------------------------------------------------------- #

def run(argv, *, env, read=None, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    code = name_redact.main(list(argv), reader=read or reader(), env=env,
                            stdin=io.StringIO(stdin), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_live_reads_the_switch_exactly():
    assert name_redact.live({"NAME_GUARD_LIVE": "true"}) is True
    for value in ("TRUE", "True", "1", "yes", " true", "true ", "", "false"):
        assert name_redact.live({"NAME_GUARD_LIVE": value}) is False
    assert name_redact.live({}) is False


@pytest.fixture
def named_file(tmp_path):
    path = tmp_path / "body.md"
    path.write_text("Summary\n\nwritten by Synthia Synthwood\n", encoding="utf-8")
    return path


def test_guard_text_file_live_refuses_and_names_no_one(named_file):
    code, out, err = run(["guard", "--text-file", str(named_file), "--owner", OWNER], env=LIVE)
    assert code == 1
    assert out == f"name-guard: refused — {named_file}:3 carries a full-name form\n"
    no_secret(out + err)


def test_guard_text_file_dry_prints_the_dry_line_and_exits_0(named_file):
    code, out, err = run(["guard", "--text-file", str(named_file), "--owner", OWNER], env=DRY)
    assert code == 0
    assert out == f"name-guard: dry — would refuse {named_file}:3 (full-name)\n"
    no_secret(out + err)


def test_guard_text_file_clean_says_ok(tmp_path):
    path = tmp_path / "clean.md"
    path.write_text("one\n[a person] two\nthree\n", encoding="utf-8")
    for env in (LIVE, DRY):
        code, out, _ = run(["guard", "--text-file", str(path), "--owner", OWNER], env=env)
        assert (code, out) == (0, "name-guard: ok — 3 lines read, nothing to refuse\n")


@pytest.mark.parametrize("args", [["--text-file", "BODY"], ["--range", "main", "HEAD"],
                                  ["--pre-push", "--default-ref", "main"]])
def test_an_unreadable_roster_exits_2_live_and_0_dry_on_every_reader(args, named_file, tmp_path):
    repo = make_repo(tmp_path)
    argv = ["guard", *[str(named_file) if a == "BODY" else a for a in args],
            "--cwd", str(repo), "--owner", OWNER]
    head = git(repo, "rev-parse", "HEAD")
    stdin = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
    code, out, err = run(argv, env=LIVE, read=unreadable("github members"), stdin=stdin)
    assert code == 2
    assert out == "name-guard: refused — roster unreadable: github members\n"
    code, out, err = run(argv, env=DRY, read=unreadable("github members"), stdin=stdin)
    assert code == 0
    assert out == "name-guard: dry — roster unreadable: github members\n"


def test_the_switch_unset_is_dry(named_file):
    code, out, _ = run(["guard", "--text-file", str(named_file), "--owner", OWNER], env={})
    assert code == 0 and out.startswith("name-guard: dry — would refuse ")


def test_an_actor_login_is_read_into_the_roster(tmp_path):
    path = tmp_path / "body.md"
    path.write_text("run by `synthactor`\n", encoding="utf-8")
    argv = ["guard", "--text-file", str(path), "--owner", OWNER]
    seen = []
    assert run(argv, env=LIVE, read=reader(seen=seen))[0] == 0
    code, out, _ = run([*argv, "--actor", ACTOR_LOGIN, "--actor", "synthbot[bot]"], env=LIVE,
                       read=reader(seen=seen))
    assert code == 1 and out == f"name-guard: refused — {path}:1 carries a login form\n"
    assert seen[-1] == {"owner": OWNER, "extra_logins": [ACTOR_LOGIN, "synthbot[bot]"]}


def test_guard_range_live_refuses_the_branchs_own_names_only(tmp_path):
    repo, first, _, head = branch_with_merge(tmp_path)
    argv = ["guard", "--range", "main", head, "--cwd", str(repo), "--owner", OWNER]
    code, out, err = run(argv, env=LIVE)
    assert code == 1
    assert out == f"name-guard: refused — commit:{first[:7]}:3 carries a full-name form\n"
    no_secret(out + err)
    code, out, _ = run(argv, env=DRY)
    assert (code, out) == (0, f"name-guard: dry — would refuse commit:{first[:7]}:3 (full-name)\n")


def test_guard_range_clean_branch_says_ok(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feature")
    head = commit(repo, {"a.md": "[a person] signed\n"}, "a clean change")
    code, out, _ = run(["guard", "--range", "main", head, "--cwd", str(repo), "--owner", OWNER],
                       env=LIVE)
    assert code == 0
    assert out == "name-guard: ok — 2 lines read, nothing to refuse\n"


def test_guard_range_git_cannot_read_is_refused_live_and_dry_otherwise(tmp_path):
    repo = make_repo(tmp_path)
    argv = ["guard", "--range", "main", "no-such-ref", "--cwd", str(repo), "--owner", OWNER]
    code, out, _ = run(argv, env=LIVE)
    assert code == 2 and out.startswith("name-guard: refused — range unreadable: ")
    code, out, _ = run(argv, env=DRY)
    assert code == 0 and out.startswith("name-guard: dry — range unreadable: ")


ZERO = "0" * 40


def pre_push_repo(tmp_path):
    """Two branches pushed at once: a new branch carrying a name, and a deletion."""
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feature")
    head = commit(repo, {"proof.md": "intro\nby `synthwood-synth`\n"}, "a proof record")
    stdin = (f"refs/heads/feature {head} refs/heads/feature {ZERO}\n"
             f"(delete) {ZERO} refs/heads/old {'f' * 40}\n")
    return repo, stdin


def test_guard_pre_push_live_refuses_a_new_branch_and_skips_a_deletion(tmp_path):
    repo, stdin = pre_push_repo(tmp_path)
    argv = ["guard", "--pre-push", "--default-ref", "main", "--cwd", str(repo), "--owner", OWNER]
    code, out, err = run(argv, env=LIVE, stdin=stdin)
    assert code == 1
    lines = out.splitlines()
    assert lines[0].startswith("name-guard: self-test ok — 1 form refused, redacted to ")
    assert lines[1:] == ["name-guard: refused — proof.md:2 carries a login form"]
    no_secret(out + err)


def test_guard_pre_push_dry_prints_dry_and_exits_0(tmp_path):
    repo, stdin = pre_push_repo(tmp_path)
    argv = ["guard", "--pre-push", "--default-ref", "main", "--cwd", str(repo), "--owner", OWNER]
    code, out, err = run(argv, env=DRY, stdin=stdin)
    assert code == 0
    assert out.splitlines()[1:] == ["name-guard: dry — would refuse proof.md:2 (login)"]
    no_secret(out + err)


def test_guard_pre_push_of_only_a_deletion_reads_nothing(tmp_path):
    repo = make_repo(tmp_path)
    stdin = f"(delete) {ZERO} refs/heads/old {'f' * 40}\n"
    argv = ["guard", "--pre-push", "--default-ref", "main", "--cwd", str(repo), "--owner", OWNER]
    code, out, _ = run(argv, env=LIVE, stdin=stdin)
    assert code == 0
    assert out.splitlines()[1:] == ["name-guard: ok — 0 lines read, nothing to refuse"]


def test_guard_pre_push_refuses_a_malformed_line_live(tmp_path):
    repo = make_repo(tmp_path)
    argv = ["guard", "--pre-push", "--default-ref", "main", "--cwd", str(repo), "--owner", OWNER]
    code, out, _ = run(argv, env=LIVE, stdin="not four fields\n")
    assert code == 2 and out.splitlines()[-1].startswith("name-guard: refused — pre-push unreadable: ")


# --------------------------------------------------------------------------- #
# E. self-test                                                                 #
# --------------------------------------------------------------------------- #

def test_self_test_prints_ok_and_never_the_form_it_built():
    for env in (LIVE, DRY):
        code, out, err = run(["self-test", "--owner", OWNER], env=env)
        assert code == 0
        assert out == "name-guard: self-test ok — 1 form refused, redacted to [the CEO]\n"
        assert err == ""
        no_secret(out + err)


def test_self_test_fails_when_a_half_fails(monkeypatch):
    monkeypatch.setattr(name_redact, "redact", lambda text, r: text)
    code, out, _ = run(["self-test", "--owner", OWNER], env=LIVE)
    assert code == 1 and out.startswith("name-guard: refused — self-test failed")
    no_secret(out)
    code, out, _ = run(["self-test", "--owner", OWNER], env=DRY)
    assert code == 0 and out.startswith("name-guard: dry — self-test failed")


def test_a_failed_self_test_refuses_the_push_live(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(name_redact, "findings", lambda lines, r: [])
    argv = ["guard", "--pre-push", "--default-ref", "main", "--cwd", str(repo), "--owner", OWNER]
    code, out, _ = run(argv, env=LIVE, stdin=f"refs/heads/x {head} refs/heads/x {ZERO}\n")
    assert code == 1 and out.splitlines()[0].startswith("name-guard: refused — self-test failed")


def test_self_test_unreadable_roster():
    assert run(["self-test", "--owner", OWNER], env=LIVE, read=unreadable())[0] == 2
    assert run(["self-test", "--owner", OWNER], env=DRY, read=unreadable())[0] == 0


# --------------------------------------------------------------------------- #
# F. the redact CLI                                                            #
# --------------------------------------------------------------------------- #

def test_redact_prints_the_redacted_text(named_file):
    code, out, err = run(["redact", "--file", str(named_file), "--owner", OWNER], env=LIVE)
    assert code == 0 and err == ""
    assert out == "Summary\n\nwritten by [a person]\n"
    assert "Synthia" in named_file.read_text(encoding="utf-8"), "the file is untouched"


def test_redact_in_place_rewrites_the_file(named_file):
    code, out, _ = run(["redact", "--file", str(named_file), "--in-place", "--owner", OWNER], env=DRY)
    assert code == 0 and out == ""
    assert named_file.read_text(encoding="utf-8") == "Summary\n\nwritten by [a person]\n"


def test_redact_keeps_crlf_byte_identical(tmp_path):
    path = tmp_path / "crlf.md"
    path.write_bytes(b"one\r\nby Synthwood\r\n")
    run(["redact", "--file", str(path), "--in-place", "--owner", OWNER], env=LIVE)
    assert path.read_bytes() == b"one\r\nby [a person]\r\n"


def test_redact_unreadable_roster_live_exits_2_and_prints_no_text(named_file):
    code, out, err = run(["redact", "--file", str(named_file), "--in-place", "--owner", OWNER],
                         env=LIVE, read=unreadable("admins: 0"))
    assert code == 2 and out == ""
    assert err == "name-guard: refused — roster unreadable: admins: 0\n"


def test_redact_unreadable_roster_dry_prints_the_text_unchanged(named_file):
    before = named_file.read_text(encoding="utf-8")
    code, out, err = run(["redact", "--file", str(named_file), "--owner", OWNER],
                         env=DRY, read=unreadable("admins: 0"))
    assert code == 0 and out == before
    assert err == "name-guard: dry — roster unreadable: admins: 0\n"


# --------------------------------------------------------------------------- #
# G. the proof-reach line                                                      #
# --------------------------------------------------------------------------- #

def test_the_proof_reach_line_is_declared():
    entries = [e for e in proof_reach.load()["log_lines"] if e["tag"] == "name-guard:"]
    assert len(entries) == 1
    assert entries[0]["printed_by"] == "scripts/name_redact.py"
    assert entries[0]["constant"] == "TAG"
    assert proof_reach.check(ROOT) == []


def test_the_module_holds_no_name():
    source = (ROOT / "scripts" / "name_redact.py").read_text(encoding="utf-8")
    assert MARKER not in source.casefold()
