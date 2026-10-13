from wizwalker import Keycode

from wiz101_auto.smoothwalk import STUCK_SECONDS, stalled, strafe_key


def test_strafe_alternates_left_then_right():
    assert strafe_key(1) == Keycode.A
    assert strafe_key(2) == Keycode.D


def test_stalled_only_after_a_moment_without_moving():
    assert stalled(5.0, STUCK_SECONDS + 0.1)
    assert not stalled(5.0, STUCK_SECONDS - 0.1)
    assert not stalled(200.0, STUCK_SECONDS + 1)
