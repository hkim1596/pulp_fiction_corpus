import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import heldout_paratext as H


def test_selftest():
    assert H.selftest()


def test_trial_issues_are_excluded_by_name():
    assert len(H.TRIAL_2F) == 16 and len(set(H.TRIAL_2F)) == 16


def test_upgrade_report_selftest():
    import upgrade_report as U
    assert U.selftest()
    assert U.reason_of("cannot safely retain cross-issue metadata for a_1930_x_a041", "a_1930_x") == "cannot safely retain cross-issue metadata"
    assert U.decade_of("x_undated_y") == "undated" and U.decade_of("x_1955_y") == "1950s"
