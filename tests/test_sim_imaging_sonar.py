"""Layout of the optional simulator imaging-sonar output of the ROS 2 node (numpy part, no ROS needed)."""
import importlib.util
import os

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_spec = importlib.util.spec_from_file_location(
    "sim_imaging_sonar", os.path.join(ROOT, "ros2", "opensonar_ros", "opensonar_ros", "sim_imaging_sonar.py"))
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)


def test_axes_and_scale():
    # opensonar R-Theta: row 0 = far, column 0 = left-most beam
    rt = np.zeros((4, 3), np.uint16)
    rt[-1, -1] = 255          # nearest range, right-most beam
    rt[0, 0] = 51             # farthest range, left-most beam
    out = sim.raw_layout(rt, 255.0)
    assert out.dtype == np.float32 and out.shape == (4, 3)
    assert out[0, 0] == 1.0                                   # simulator: row 0 = nearest, column 0 = right-most
    assert abs(out[-1, -1] - 0.2) < 1e-6
    assert out.max() <= 1.0 and out.min() >= 0.0


def test_frames_of_both_families():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "src"))
    sys.path.insert(0, os.path.dirname(__file__))
    import opensonar
    from opensonar.frame import to_rtheta
    from test_aris import write_aris
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        data = np.zeros((50, 48), np.uint8)
        data[0, 0] = 200                                      # .aris: nearest sample of beam 0 (right-most)
        path = os.path.join(d, "t.aris")
        write_aris(path, [data])
        with opensonar.open(path) as s:
            img, _ = to_rtheta(s.heads[0].ping(0))
    out = sim.raw_layout(img, 255.0)
    assert np.unravel_index(np.argmax(out), out.shape) == (0, 0)  # beam 0 stays column 0, nearest row 0
    assert abs(out[0, 0] - 200 / 255) < 1e-6
    assert sim.load_type("") is None and sim.load_type("no_such_pkg/msg/Nothing") is None
