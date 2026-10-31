import numpy as np

from gesture_control.filters import OneEuroFilter

DT = 1 / 30


def test_first_sample_passes_through():
    f = OneEuroFilter(1.0, 1.0)
    assert f(np.array([0.5]), 0.0)[0] == 0.5


def test_reduces_jitter_on_a_still_signal():
    rng = np.random.default_rng(0)
    noisy = 0.5 + rng.normal(0, 0.005, 300)
    f = OneEuroFilter(1.2, 8.0)
    smoothed = np.array([f(np.array([v]), i * DT)[0] for i, v in enumerate(noisy)])
    assert smoothed[30:].std() < noisy[30:].std() / 2


def test_follows_fast_motion_with_little_lag():
    # A quick move across the frame in 0.33s, as in a fast cursor flick.
    f = OneEuroFilter(1.2, 8.0)
    out = 0.0
    for i in range(11):
        out = f(np.array([i * 0.08]), i * DT)[0]
    assert out > 0.8 * 0.7


def test_filters_each_element_independently():
    f = OneEuroFilter(1.0, 1.0)
    f(np.zeros((21, 3)), 0.0)
    result = f(np.ones((21, 3)), DT)
    assert result.shape == (21, 3)


def test_reset_forgets_history():
    f = OneEuroFilter(1.0, 0.0)
    f(np.array([0.0]), 0.0)
    f.reset()
    assert f(np.array([1.0]), DT)[0] == 1.0


def test_repeated_timestamp_does_not_divide_by_zero():
    f = OneEuroFilter(1.0, 1.0)
    f(np.array([0.2]), 1.0)
    assert np.isfinite(f(np.array([0.9]), 1.0)).all()
