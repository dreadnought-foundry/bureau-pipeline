"""Throwaway test for DRE-6476, the staging card of the DRE-6353 proof.

It exists only on agent/DRE-6476-lost-push, whose pull request targets the
throwaway base proof/DRE-4911-staging-base. It is deleted with the branch.
"""


def test_two_plus_two():
    # The right value is 4. This assertion is wrong on purpose.
    assert 2 + 2 == 5
