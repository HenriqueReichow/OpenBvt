"""Optional output in the imaging-sonar message layout of a simulator, so a digital twin can take real
and simulated sonars the same way. Works for both sonar families (P900/M900 and .aris).

The message type is a parameter (`sim_imaging_sonar_type`, "<package>/msg/<Type>"); when it is empty or
the package is not installed, the node runs without this topic. Fields used:

    std_msgs/Header header
    int32 bins_azimuth, bins_range     the size of this ping's image
    sensor_msgs/Image raw_image        32FC1, 0–1; rows = range (row 0 = nearest),
                                       columns = azimuth (column 0 = the right-most / starboard beam)
    sensor_msgs/Image intensity        ground truth: EMPTY with a real sonar
    sensor_msgs/Image elevation        ground truth: EMPTY with a real sonar
    sensor_msgs/PointCloud2 point_cloud  ground truth: EMPTY with a real sonar

The simulator measures azimuth as atan2(−y, x) with y to the right, i.e. positive to the LEFT, and
fills columns from the minimum azimuth: column 0 is the right-most beam. The opensonar R-Theta image has
row 0 = far range and column 0 = the left-most beam, so both axes are flipped.
"""
from array import array

import numpy as np


def raw_layout(rtheta, full_scale):
    """opensonar R-Theta image (row 0 = far, column 0 = left-most) → float32 0–1, rows near → far,
    columns right-most → left-most."""
    img = np.asarray(rtheta, np.float32)[::-1, ::-1] / np.float32(full_scale)
    return np.ascontiguousarray(np.clip(img, 0.0, 1.0))


def load_type(name):
    """The message class for "<package>/msg/<Type>", or None (empty name, or package not installed)."""
    if not name:
        return None
    try:
        from rosidl_runtime_py.utilities import get_message
        return get_message(name)
    except (AttributeError, ModuleNotFoundError, ValueError):
        return None


def fill(msg, header, rtheta, full_scale):
    """Fills `msg` (an instance of the loaded type) from one ping; ground-truth fields stay empty."""
    from sensor_msgs.msg import Image
    raw = raw_layout(rtheta, full_scale)
    msg.header = header
    msg.bins_range, msg.bins_azimuth = int(raw.shape[0]), int(raw.shape[1])
    msg.raw_image = Image(header=header, height=int(raw.shape[0]), width=int(raw.shape[1]), encoding="32FC1",
                          is_bigendian=0, step=int(raw.shape[1]) * 4)
    msg.raw_image.data = array("B", raw.tobytes())
    return msg
