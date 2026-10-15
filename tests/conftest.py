# pytest-qt is loaded here when plugin autoloading is off (PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 on machines with ROS)
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
    try:
        import pytestqt  # noqa: F401
        pytest_plugins = ["pytestqt.plugin"]
    except ImportError:
        pass
