"""Stand-in for an agent-bureau test that reads nothing from the pipeline
(DRE-6496 fixture). The mirror check must not discover it: its text names
neither the pipeline directory variable nor the pipeline checkout."""


def test_arithmetic_still_holds():
    assert 2 + 2 == 4
