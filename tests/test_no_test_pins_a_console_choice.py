"""No test pins a choice the console can change (DRE-6866).

The console changes a choice on every save: which model leads a group, which
group an agent is in, which agents exist, which projects have a model of their
own. Since DRE-6939 those choices live in the home, not in
`config/models.yaml`. A test that asserts one of them as a literal goes red on
a legitimate save. The rewrite cards (DRE-6860 to DRE-6864, DRE-6884,
DRE-6885) removed the pins that existed; this guard keeps new ones out.

It reads what the console can change off the frozen home document
(`model_fixture.fixture_home()`) — the models of every group, the agents in
`agents` and `added_agents` with the session agents the console may put in a
group, and the group keys — and scans exactly the files
`model_fixture.model_reading_tests(ROOT)` names, the set the harness
(DRE-6865) runs inside each changed copy. In each file, every `assert`
statement and every `self.assert*` call is a candidate, with a module-level
constant bound to a string literal read as that string. Four shapes are
findings:

* a model id of one of the document's groups;
* an agent name beside a group key — an agent-to-group pair;
* a list or tuple literal of two or more agent names;
* the word `projects` beside `{}`.

An id in no group (`retired`, `excluded`) is not a finding. The only way past
the guard is `ALLOWED`, one entry per test with the reason its literal is not
a pin; an entry whose test is gone or produces no finding fails as stale, so
the list cannot grow silently.
"""
from __future__ import annotations

import ast
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import model_fixture  # noqa: E402

LADDER_ID = "ladder id"
AGENT_GROUP = "agent-to-group pair"
AGENT_LIST = "list of agents"
EMPTY_PROJECTS = "empty projects"

# Why a literal is not a pin, one sentence per kind of test that holds one.
_OWN_INPUT = (
    "The id is the test's own input, handed to the code under test as an "
    "argument, an environment value or a recorded run and read back from its "
    "output, so no console save reaches it.")
_ABSENT = (
    "The id is asserted absent: the text under test must name no model, and "
    "no console save can make it name one.")
_SAMPLE_REGION = (
    "The id sits in a sample generated region the test writes as its own diff "
    "for the commit-order check, which reads that text and never a model file.")
_CLI_TABLE = (
    "The id is the model the test asks the Claude Code pin tool about, "
    "answered from a pinned release's own model table or a faked Models API "
    "reply, and neither is a console choice.")
_CLI_RECORD = (
    "The id is the one the test's own pin record names, written into the "
    "minimum-version table of a copied tree and read back from it.")
_PRICE = (
    "The id keys config/model-prices.yaml, the declared vendor price list, "
    "which the console reads and never writes.")
_OWN_LADDERS = (
    "The ladders, the catalog and the prices are built by the test itself, so "
    "the id is its own fixture and not the committed config.")
_FROZEN_LADDERS = (
    "The expected rungs are members of the frozen fixture's ladders, which the "
    "test classifies the candidate against and no console save touches.")
_FROZEN_CAUTIONS = (
    "The ids are read against the frozen fixture's ladders, prices and "
    "set-aside list, the files of 2026-10-10, which no console save touches.")
_FROZEN_AGENTS = (
    "The agents are read in the frozen fixture tree the test builds from "
    "tests/fixtures/model-config-frozen, which no console save touches.")
_FROZEN_CHANGES = (
    "The change set is applied to the frozen fixture's models.yaml of "
    "2026-10-10 and the ids and projects are read back from the text it "
    "writes, which no console save touches.")
_ABSENT_KEY = (
    "The test deletes `projects` from a frozen-fixture config and asserts the "
    "normalizer's default for an absent key, not the committed map.")
_OWN_MOVE = (
    "The agent and group are the move the test's own change set makes, read "
    "back from the file that change set wrote.")
_RULES_BLOCK = (
    "The names are the contracted values of config/models.yaml's `rules:` "
    "block (DRE-6992), which the console reads and never writes.")
_OWN_RULES = (
    "The names are the `rules:` block the test writes into a frozen-fixture "
    "tree itself and reads back from that tree's module; no console save "
    "touches it.")

# `"tests/<file>.py::<Class.test_name or test_name>"` -> why the literal in
# that test is not a pin. One entry per test; every entry is checked to still
# produce a finding.
ALLOWED: dict[str, str] = {
    "tests/test_agents_registry.py::AgentsRegistryTest.test_only_a_present_null_model_is_scripted":
        _OWN_INPUT,
    "tests/test_card_snapshot.py::test_off_answers_what_the_two_reads_answered": _OWN_INPUT,
    "tests/test_claude_code_pin.py::test_apply_puts_a_known_models_row_in_the_minimum_table":
        _CLI_RECORD,
    "tests/test_claude_code_pin.py::test_apply_updates_an_existing_minimum_row_rather_than_adding_a_second":
        _CLI_RECORD,
    "tests/test_claude_code_pin.py::test_latest_supporting_finds_2_1_284_and_v1_0_236": _CLI_TABLE,
    "tests/test_claude_code_pin.py::test_limits_prints_the_models_api_numbers": _CLI_TABLE,
    "tests/test_claude_code_pin.py::test_limits_that_cannot_be_read_are_unknown": _CLI_TABLE,
    "tests/test_claude_code_pin.py::test_strength_at_full_limits_is_full": _CLI_TABLE,
    "tests/test_claude_code_pin.py::test_strength_uses_the_one_modelusage_seam": _CLI_TABLE,
    "tests/test_claude_code_pin.py::test_strength_without_both_halves_is_unknown": _CLI_TABLE,
    "tests/test_claude_code_pin.py::test_supports_answers_no_on_2_1_282": _CLI_TABLE,
    "tests/test_claude_code_pin.py::test_the_2_1_282_table_does_not_list_sonnet_5_5": _CLI_TABLE,
    "tests/test_claude_code_pin.py::test_the_2_1_284_table_lists_sonnet_5_5_at_1m_and_128k":
        _CLI_TABLE,
    "tests/test_claude_code_pin.py::test_the_decoy_picker_list_is_not_read_as_the_registry":
        _CLI_TABLE,
    "tests/test_credential_expiry_scenario.py::TheReportStepSaysCredentialNotDied.test_it_records_no_model_as_having_failed":
        _ABSENT,
    "tests/test_dead_run_split_first.py::TestTheOtherClassesStillHold.test_an_api_death_holds_with_main_s_text":
        _OWN_INPUT,
    "tests/test_groom_verify_agent.py::test_cost_duration_and_model_come_from_the_execution_file":
        _OWN_INPUT,
    "tests/test_groomer_receipt.py::BudgetAndCutTest.test_both_keys_read_in_the_contracted_order":
        _OWN_INPUT,
    "tests/test_groomer_receipt.py::ShippedKeysOnlyTest.test_a_degraded_receipt_travels_verbatim":
        _OWN_INPUT,
    "tests/test_groomer_receipt.py::ShippedKeysOnlyTest.test_the_line_names_the_model_that_answered_and_the_call_count":
        _OWN_INPUT,
    "tests/test_harness_agent_run_prompt.py::TestThePromptDoesNotTransitArgv.test_the_argv_carries_the_bounds_and_not_the_prompt":
        _OWN_INPUT,
    "tests/test_model_adoption.py::test_a_dearer_side_is_ask_not_adopt": _OWN_LADDERS,
    "tests/test_model_adoption.py::test_a_ladder_pinned_to_an_alias_resolves_through_its_dated_snapshot":
        _OWN_LADDERS,
    "tests/test_model_adoption.py::test_an_extra_entry_for_a_retired_id_is_allowed": _PRICE,
    "tests/test_model_adoption.py::test_load_prices_reads_the_committed_file": _PRICE,
    "tests/test_model_adoption.py::test_load_prices_rejects_an_entry_missing_a_side": _OWN_INPUT,
    "tests/test_model_adoption.py::test_the_newer_opus_with_no_declared_price_is_ask":
        _FROZEN_LADDERS,
    "tests/test_model_caution.py::test_a_dearer_model_names_both_prices": _FROZEN_CAUTIONS,
    "tests/test_model_caution.py::test_a_standing_exception_is_current_when_given":
        _FROZEN_CAUTIONS,
    "tests/test_model_caution.py::test_dearer_on_one_side_is_dearer": _FROZEN_CAUTIONS,
    "tests/test_model_caution.py::test_model_label_moved_and_the_generator_imports_it":
        _OWN_INPUT,
    "tests/test_model_caution.py::test_set_aside_date_is_the_first_date_in_the_excluded_reason":
        _FROZEN_CAUTIONS,
    "tests/test_model_caution.py::test_the_current_model_and_a_cheaper_one_carry_no_caution":
        _FROZEN_CAUTIONS,
    "tests/test_model_caution.py::test_the_dearest_model_for_builders_names_the_busiest_group":
        _FROZEN_CAUTIONS,
    "tests/test_model_caution.py::test_the_nine_cautioned_pairs_of_2026_10_10": _FROZEN_CAUTIONS,
    "tests/test_model_config.py::ConfigDrivesTheFleetTest.test_selector_degrades_to_the_mirror_when_the_config_is_unreadable":
        _FROZEN_AGENTS,
    "tests/test_model_config_change.py::test_a_project_builder_on_a_reviewer_model_says_where_it_is_reviewed":
        _FROZEN_CHANGES,
    "tests/test_model_config_change.py::test_an_added_group_on_fable_5_1_is_cautioned_against_the_builders_default":
        _FROZEN_CAUTIONS,
    "tests/test_model_config_change.py::test_clearing_the_only_exception_leaves_projects_empty_and_sets_aside":
        _FROZEN_CHANGES,
    "tests/test_model_config_change.py::test_replacing_a_project_choice_sets_the_replaced_model_aside_again":
        _FROZEN_CHANGES,
    "tests/test_model_config_change.py::test_reviewers_on_opus_5_5_say_where_an_opus_build_is_reviewed":
        _FROZEN_CHANGES,
    "tests/test_model_config_change.py::test_the_dre_6789_ids_are_where_the_frozen_file_puts_them":
        _FROZEN_CHANGES,
    "tests/test_model_effort.py::PriceTest.test_opus_5_5_is_declared_at_4_and_20": _PRICE,
    "tests/test_model_effort.py::PriceTest.test_sonnet_5_5_is_declared_at_the_price_of_sonnet_5":
        _PRICE,
    "tests/test_model_projects_schema.py::TestNormalizedShape.test_a_kind_with_no_label_or_purpose_gets_the_defaults":
        _ABSENT_KEY,
    "tests/test_model_rules_and_markers.py::test_a_planner_the_block_admits_may_hold_the_judgement_kind":
        _OWN_RULES,
    "tests/test_model_rules_and_markers.py::test_a_reviewer_the_block_adds_is_held_to_rule_3":
        _OWN_RULES,
    "tests/test_model_rules_and_markers.py::test_the_committed_config_carries_the_rules_block":
        _RULES_BLOCK,
    "tests/test_model_trial.py::test_the_degraded_summary_names_both_numbers_on_both_sides":
        _OWN_INPUT,
    "tests/test_model_trial.py::test_the_same_run_at_full_limits_passes": _OWN_INPUT,
    "tests/test_plan_critic.py::TheReviewTurnsCli.test_it_prints_the_one_line_for_the_run_that_was_cut_off":
        _OWN_INPUT,
    "tests/test_plan_critic.py::TheReviewTurnsReceipt.test_it_parses_back_oldest_to_newest":
        _OWN_INPUT,
    "tests/test_plan_critic.py::TheReviewTurnsReceipt.test_the_line_carries_the_four_facts_in_order":
        _OWN_INPUT,
    "tests/test_plan_critic.py::TheTwoProbeBodiesRunTheRoute.test_fd_6_is_stamped_one_off_and_the_critic_still_stops_it":
        _OWN_INPUT,
    "tests/test_plan_critic_wiring.py::TheReviewSaysWhatItSpent.test_the_posted_body_is_the_receipt_line_and_nothing_else":
        _OWN_INPUT,
    "tests/test_planning_classify.py::TestThePromptDoesNotTransitArgv.test_the_argv_carries_the_bounds_and_not_the_prompt":
        _OWN_INPUT,
    "tests/test_planning_classify.py::TestTheSeamReportsACut.test_an_ordinary_api_answer_is_not_cut":
        _OWN_INPUT,
    "tests/test_planning_classify.py::TestTheSeamReportsACut.test_the_api_path_reads_the_cut_off_its_stop_reason":
        _OWN_INPUT,
    "tests/test_planning_classify.py::TestTheTransport.test_the_claude_code_call_is_bounded_and_carries_no_tools":
        _OWN_INPUT,
    "tests/test_planning_classify.py::TestTheTransport.test_the_model_that_actually_answered_is_what_is_recorded":
        _OWN_INPUT,
    "tests/test_planning_classify.py::TestTheTwoReasons.test_the_one_off_probe_that_started_this_is_stamped_one_off":
        _OWN_INPUT,
    "tests/test_planning_classify.py::TestWhoStampedIt.test_the_planner_stamp_carries_by_and_the_model":
        _OWN_INPUT,
    "tests/test_planning_classify_continuation.py::test_a_one_message_stream_reads_exactly_as_before":
        _OWN_INPUT,
    "tests/test_platform_fault_not_a_model_death.py::PreAgentDecisionTest.test_it_wins_over_the_turn_and_api_classes":
        _OWN_INPUT,
    "tests/test_platform_fault_not_a_model_death.py::PreAgentDecisionTest.test_it_writes_no_model_error_marker":
        _ABSENT,
    "tests/test_platform_fault_scenario.py::PlatformFaultScenario.test_the_receipt_names_no_model_as_having_failed":
        _ABSENT,
    "tests/test_qa_review_model_note.py::LinearCopyCarriesTheModelTest.test_the_linear_copy_carries_the_model_and_the_reason":
        _OWN_INPUT,
    "tests/test_qa_review_model_note.py::PrCopyIsUntouchedTest.test_the_model_line_never_reaches_the_pr_comment":
        _ABSENT,
    "tests/test_session_agents.py::test_a_session_agent_can_be_moved_again_from_group_to_group":
        _OWN_MOVE,
    "tests/test_session_agents.py::test_the_change_set_of_run_38114763170_validates_and_applies":
        _OWN_MOVE,
    "tests/test_tdd_commit_check.py::ClassifyPathGeneratedRegionTest.test_a_path_with_no_proving_generator_stays_code":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::ClassifyPathGeneratedRegionTest.test_generated_region_change_classifies_as_docs":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::ClassifyPathGeneratedRegionTest.test_marker_edit_stays_code":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::ClassifyPathGeneratedRegionTest.test_region_plus_an_outside_line_stays_code":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::GeneratedRegionChangeTest.test_a_line_outside_the_region_is_not":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::GeneratedRegionChangeTest.test_a_neighbour_of_a_proved_file_is_not_covered":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::GeneratedRegionChangeTest.test_a_region_with_no_closing_marker_is_not":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::GeneratedRegionChangeTest.test_added_file_has_no_base_version_and_is_not_generated":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::GeneratedRegionChangeTest.test_deleted_file_has_no_head_version_and_is_not_generated":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::GeneratedRegionChangeTest.test_deleting_the_region_is_not":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::GeneratedRegionChangeTest.test_editing_a_marker_line_is_not":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::GeneratedRegionChangeTest.test_regenerated_region_is_a_generated_change":
        _SAMPLE_REGION,
    "tests/test_tdd_commit_check.py::GeneratedRegionChangeTest.test_unparseable_after_fails_closed_to_code":
        _SAMPLE_REGION,
    "tests/test_turn_exhaustion_not_outage.py::BuildRunTurnExhaustionTest.test_api_death_path_unchanged":
        _OWN_INPUT,
    "tests/test_turn_exhaustion_not_outage.py::BuildRunTurnExhaustionTest.test_model_fallback_cannot_fire_on_an_exhaustion_receipt":
        _OWN_INPUT,
    "tests/test_turn_exhaustion_not_outage.py::BuildRunTurnExhaustionTest.test_requeue_records_no_model_error_marker":
        _ABSENT,
}


@dataclass(frozen=True)
class Choices:
    """What the console can change, read off a home document."""

    ids: frozenset[str]
    agents: frozenset[str]
    kinds: frozenset[str]

    @classmethod
    def of(cls, doc: dict, session=()) -> "Choices":
        """The choices `doc` holds. `session` names the session agents, which
        are in no group until the console puts one there."""
        groups = doc.get("groups") or {}
        ids = {m for key in groups for m in model_fixture.ladder(doc, key)}
        agents = (set(doc.get("agents") or {}) | set(doc.get("added_agents") or {})
                  | set(session))
        return cls(frozenset(ids), frozenset(agents), frozenset(groups))

    @classmethod
    def frozen(cls) -> "Choices":
        """The choices of the frozen home document, with the frozen files'
        session agents."""
        return cls.of(model_fixture.fixture_home(),
                      model_fixture.fixture_config().get("session_agents") or ())

    def id_pattern(self) -> re.Pattern:
        # Longest first, and bounded, so `claude-opus-5` is not read inside
        # `claude-opus-5-5`, nor an excluded id inside a ladder id's name.
        names = sorted(self.ids, key=len, reverse=True)
        return re.compile(r"(?<![\w.-])(" + "|".join(map(re.escape, names)) + r")(?![\w.]|-\w)")


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    shape: str
    literal: str
    test: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.shape} — {self.literal}"

    @property
    def key(self) -> str:
        return f"{self.path}::{self.test}"


def _constants(module: ast.Module) -> dict[str, str]:
    """Module-level names bound to a string literal."""
    found = {}
    for node in module.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if (isinstance(target, ast.Name) and isinstance(value, ast.Constant)
                and isinstance(value.value, str)):
            found[target.id] = value.value
    return found


def _is_assertion(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
            and node.func.attr.startswith("assert"))


def _assertions(module: ast.Module):
    """Every assertion with the test it sits in: `Class.test_name`,
    `test_name`, or `<module>` outside any function."""
    def walk(node: ast.AST, scope: list[str], in_function: bool):
        for child in ast.iter_child_nodes(node):
            if _is_assertion(child):
                yield child, ".".join(scope) or "<module>"
            if isinstance(child, ast.ClassDef) and not in_function:
                yield from walk(child, scope + [child.name], False)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not in_function:
                yield from walk(child, scope + [child.name], True)
            else:
                yield from walk(child, scope, in_function)
    yield from walk(module, [], False)


def _tests(module: ast.Module) -> set[str]:
    """The qualified names `_assertions` can attribute a finding to."""
    names = {"<module>"}

    def walk(node: ast.AST, scope: list[str]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                names.add(".".join(scope + [child.name]))
                walk(child, scope + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(".".join(scope + [child.name]))
    walk(module, [])
    return names


def _strings(node: ast.AST, constants: dict[str, str]):
    """(node, string, the constant it came through or None) for every string
    in `node`, a name bound to a module-level string read as that string."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub, sub.value, None
        elif isinstance(sub, ast.Name) and sub.id in constants:
            yield sub, constants[sub.id], sub.id


def scan_file(path: Path, rel: str, choices: Choices) -> list[Finding]:
    """The findings in one test file."""
    source = path.read_text(encoding="utf-8", errors="replace")
    module = ast.parse(source, filename=rel)
    constants = _constants(module)
    ids = choices.id_pattern()
    found: set[Finding] = set()
    for assertion, test in _assertions(module):
        strings = list(_strings(assertion, constants))
        for node, text, via in strings:
            for model in dict.fromkeys(ids.findall(text)):
                literal = f"{model} (through {via})" if via else model
                found.add(Finding(rel, node.lineno, LADDER_ID, literal, test))
        agents = list(dict.fromkeys(t for _, t, _ in strings if t in choices.agents))
        kinds = list(dict.fromkeys(t for _, t, _ in strings if t in choices.kinds))
        if agents and kinds:
            found.add(Finding(rel, assertion.lineno, AGENT_GROUP,
                              ", ".join(agents + kinds), test))
        for node in ast.walk(assertion):
            if isinstance(node, (ast.List, ast.Tuple)):
                names = [t for e in node.elts for _, t, _ in _strings(e, constants)
                         if isinstance(e, (ast.Constant, ast.Name)) and t in choices.agents]
                if len(names) >= 2:
                    found.add(Finding(rel, node.lineno, AGENT_LIST, repr(names), test))
        segment = ast.get_source_segment(source, assertion) or ""
        if re.search(r"\bprojects\b", segment):
            for node in ast.walk(assertion):
                if isinstance(node, ast.Dict) and not node.keys:
                    found.add(Finding(rel, node.lineno, EMPTY_PROJECTS, "projects, {}", test))
    return sorted(found)


def scan(tree: Path) -> list[Finding]:
    """Every finding in the files `model_fixture.model_reading_tests(tree)`
    names, read against the choices of the frozen home document."""
    tree = Path(tree)
    choices = Choices.frozen()
    found = []
    for path in model_fixture.model_reading_tests(tree):
        found += scan_file(path, path.relative_to(tree).as_posix(), choices)
    return sorted(found)


def guard(tree: Path, allowed: dict[str, str]) -> list[str]:
    """Every finding `allowed` does not cover, and every stale entry of
    `allowed`. Empty when the tree holds no pin."""
    tree = Path(tree)
    findings = scan(tree)
    problems = [str(f) for f in findings if f.key not in allowed]
    hit = {f.key for f in findings}
    for key in sorted(allowed):
        if key in hit:
            continue
        rel, _, test = key.partition("::")
        path = tree / rel
        exists = path.is_file() and test in _tests(ast.parse(path.read_text(encoding="utf-8")))
        why = "produces no finding" if exists else "names no test that exists"
        problems.append(f"stale ALLOWED entry {key}: {why}")
    return problems


# --------------------------------------------------------------------------- #
# The guard, on the real tree                                                  #
# --------------------------------------------------------------------------- #

def test_no_test_pins_a_console_choice():
    problems = guard(ROOT, ALLOWED)
    assert not problems, (
        f"{len(problems)} assertion(s) pin a choice the console can change, or "
        "an ALLOWED entry is stale. Derive the value from the config a test "
        "reads (tests/model_fixture.py), or add an ALLOWED entry saying why the "
        "literal is not a pin:\n" + "\n".join(problems))


def test_every_allowed_entry_carries_a_reason():
    for key, reason in ALLOWED.items():
        assert re.fullmatch(r"tests/[\w/]+\.py::[\w.<>]+", key), key
        assert len(reason.split()) >= 5 and reason.rstrip().endswith("."), (
            f"{key}: the reason must be a sentence saying why the literal is not a pin")


# --------------------------------------------------------------------------- #
# The guard's own correctness, on a planted tree                               #
# --------------------------------------------------------------------------- #

def _doc() -> dict:
    return model_fixture.fixture_home()


def _plant(tmp_path: Path, files: dict[str, str]) -> Path:
    """A tree holding the frozen `config/models.yaml` — whose rung ids say
    which files are scanned — and `files` under `tests/`."""
    (tmp_path / "config").mkdir()
    shutil.copy2(model_fixture.FIXTURE_DIR / "models.yaml",
                 tmp_path / "config" / "models.yaml")
    (tmp_path / "tests").mkdir()
    for name, text in files.items():
        (tmp_path / "tests" / name).write_text(text)
    return tmp_path


def _line(text: str, marker: str) -> int:
    return next(n for n, line in enumerate(text.splitlines(), 1) if marker in line)


def test_each_shape_is_reported_by_file_and_line(tmp_path):
    doc = _doc()
    model = model_fixture.ladder(doc, next(iter(doc["groups"])))[0]
    agent, kind = next(iter(doc["agents"].items()))
    first, second = list(doc["agents"])[:2]
    text = (
        '"""Reads config/models.yaml."""\n'
        "class PlantedTest:\n"
        "    def test_model(self):\n"
        f'        self.assertEqual(pick(), "{model}")  # shape-1\n'
        "    def test_group(self):\n"
        f'        self.assertEqual(cfg["agents"]["{agent}"], "{kind}")  # shape-2\n'
        "\n"
        "def test_agents():\n"
        f'    assert names() == ["{first}", "{second}"]  # shape-3\n'
        "\n"
        "def test_projects():\n"
        '    assert cfg["projects"] == {}  # shape-4\n'
    )
    tree = _plant(tmp_path, {"test_planted.py": text})

    problems = guard(tree, {})

    at = "tests/test_planted.py"
    assert problems == [
        f"{at}:{_line(text, 'shape-1')}: {LADDER_ID} — {model}",
        f"{at}:{_line(text, 'shape-2')}: {AGENT_GROUP} — {agent}, {kind}",
        f"{at}:{_line(text, 'shape-3')}: {AGENT_LIST} — {[first, second]!r}",
        f"{at}:{_line(text, 'shape-4')}: {EMPTY_PROJECTS} — projects, {{}}",
    ]
    assert [f.key for f in scan(tree)] == [
        f"{at}::PlantedTest.test_model", f"{at}::PlantedTest.test_group",
        f"{at}::test_agents", f"{at}::test_projects",
    ]


def test_an_id_reached_through_a_module_constant_is_caught(tmp_path):
    doc = _doc()
    model = model_fixture.ladder(doc, next(iter(doc["groups"])))[-1]
    text = (
        '"""Reads config/models.yaml."""\n'
        f'PINNED = "{model}"\n'
        "\n"
        "def test_pick():\n"
        "    assert pick() == PINNED  # here\n"
    )
    tree = _plant(tmp_path, {"test_planted.py": text})

    assert guard(tree, {}) == [
        f"tests/test_planted.py:{_line(text, '# here')}: {LADDER_ID} — {model} (through PINNED)"]


def test_a_file_naming_only_a_ladder_id_is_scanned(tmp_path):
    doc = _doc()
    model = model_fixture.ladder(doc, list(doc["groups"])[-1])[0]
    text = f'def test_pick():\n    assert pick() == "{model}"\n'
    assert not any(name in text for name in model_fixture.MODEL_FILE_NAMES)
    tree = _plant(tmp_path, {"test_step.py": text})

    assert guard(tree, {}) == [f"tests/test_step.py:2: {LADDER_ID} — {model}"]


def test_an_assertion_reading_both_sides_from_the_config_is_not_reported(tmp_path):
    text = (
        '"""Reads config/models.yaml."""\n'
        "def test_first_rung(cfg, kind, agent):\n"
        '    ladder = cfg["ladders"][cfg["kinds"][kind]["ladder"]]\n'
        '    assert ladder[0] == cfg["ladders"][cfg["kinds"][cfg["agents"][agent]]["ladder"]][0]\n'
        '    assert sorted(cfg["projects"]) == sorted(choices["projects"])\n'
    )
    tree = _plant(tmp_path, {"test_derived.py": text})

    assert guard(tree, {}) == []


def test_an_id_on_no_ladder_is_not_reported(tmp_path):
    # Retired and excluded are code-side facts of the frozen models.yaml.
    cfg = model_fixture.fixture_config()
    off = [e["model"] if isinstance(e, dict) else e
           for e in (cfg.get("retired") or []) + (cfg.get("excluded") or [])]
    assert off
    text = '"""Reads config/models.yaml."""\ndef test_off():\n' + "".join(
        f'    assert retired() == "{model}"\n' for model in off)
    tree = _plant(tmp_path, {"test_off.py": text})

    assert guard(tree, {}) == []


def test_an_allowed_test_passes_and_a_stale_entry_fails(tmp_path):
    doc = _doc()
    model = model_fixture.ladder(doc, next(iter(doc["groups"])))[0]
    text = (
        '"""Reads config/models.yaml."""\n'
        "class PlantedTest:\n"
        "    def test_message(self):\n"
        f'        self.assertIn("{model}", error())\n'
        "    def test_clean(self):\n"
        "        self.assertTrue(error())\n"
    )
    tree = _plant(tmp_path, {"test_planted.py": text})
    reason = "The policy error is asserted to name the offending id."
    at = "tests/test_planted.py"

    assert guard(tree, {f"{at}::PlantedTest.test_message": reason}) == []
    assert guard(tree, {
        f"{at}::PlantedTest.test_message": reason,
        f"{at}::PlantedTest.test_gone": reason,
        f"{at}::PlantedTest.test_clean": reason,
        "tests/test_missing.py::test_anything": reason,
    }) == [
        "stale ALLOWED entry tests/test_missing.py::test_anything: names no test that exists",
        f"stale ALLOWED entry {at}::PlantedTest.test_clean: produces no finding",
        f"stale ALLOWED entry {at}::PlantedTest.test_gone: names no test that exists",
    ]
