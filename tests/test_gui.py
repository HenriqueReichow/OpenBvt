"""GUI (PySide6 + pyqtgraph, offscreen) against the service and the emulator: it starts/stops the service
by itself, discovers, connects by IP or file, shows the selected heads and applies the controls."""
import os
import socket
import sys
import tempfile
import threading
import time

import numpy as np
import pytest

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("zmq")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("pytestqt")

from PySide6 import QtCore  # noqa: E402

from opensonar.gui import Service, build  # noqa: E402
from opensonar.server import Server  # noqa: E402

FILE = os.path.join(ROOT, "samples", "exp1.son")
needs_file = pytest.mark.skipif(not os.path.exists(FILE), reason="no samples/exp1.son")


def _free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


def _emulator(discovery=False):
    from sonar_emulator import Emulator
    emu = Emulator(FILE, compress_signal=True)
    emu.quiet = True
    threading.Thread(target=emu.serve_tcp, args=(0,), daemon=True).start()
    while emu.tcp_port is None:
        time.sleep(0.01)
    emu.stop = threading.Event()
    if discovery:
        threading.Thread(target=emu.serve_discovery, args=(emu.tcp_port, emu.stop), daemon=True).start()
    return emu


@pytest.fixture
def server():
    srv = Server("127.0.0.1", 0, 0)
    done = threading.Event()
    t = threading.Thread(target=srv.serve, args=(done,), daemon=True)
    t.start()
    yield srv
    done.set()
    t.join(10)


@pytest.fixture
def window(qtbot, server):
    d = tempfile.TemporaryDirectory()
    settings = QtCore.QSettings(os.path.join(d.name, "gui.ini"), QtCore.QSettings.IniFormat)
    service = Service(rep_port=server.rep_port, pub_port=server.pub_port, spawn=False)
    w = build(service, settings)
    qtbot.addWidget(w)
    yield w
    w.close()
    d.cleanup()


def test_service_is_started_and_stopped_only_when_owned():
    rep, pub = _free_ports(2)
    owner = Service(rep_port=rep, pub_port=pub)
    assert owner.owned and owner.process.poll() is None
    guest = Service(rep_port=rep, pub_port=pub)                 # a second GUI finds it running
    assert not guest.owned
    guest.stop()                                                # does not stop someone else's service
    assert owner.client.status()["version"] == 1
    p = owner.process
    owner.stop()
    assert p.poll() is not None                                 # the owner stops it


def test_window_leaves_an_external_service_running(window, server):
    window.close()
    assert window.service.owned is False
    assert Service(rep_port=server.rep_port, pub_port=server.pub_port, spawn=False).client.status()["version"] == 1


def _wait_image(qtbot, view, timeout=10000):
    qtbot.waitUntil(lambda: view.last is not None, timeout=timeout)
    return view.last


@needs_file
def test_connect_by_ip_show_and_controls(qtbot, window, server):
    emu = _emulator()
    w = window
    w.ip.setText(f"127.0.0.1:{emu.tcp_port}")
    w.btn_connect_ip.click()
    assert w.info["serial"] == "2058"
    assert w.heads.count() == 1 and "P900-130" in w.heads.item(0).text() and "kHz" in w.heads.item(0).text()
    assert list(w.views) == [0]
    assert w.settings.value("last_ip") == f"127.0.0.1:{emu.tcp_port}"
    # range from the fields
    w.range_stop.setValue(5.0)
    w.range_stop.editingFinished.emit()
    w.btn_start.click()
    v = w.views[0]
    qtbot.waitUntil(lambda: v.last is not None and abs(v.last[2].ping.range_stop - 5.0) < 0.06, timeout=10000)
    img, im, msg = v.last
    assert img.shape[0] * im["res"] < 5.3                       # cropped to the ping range
    m = w.mappers[0]
    assert m.auto and (m.bottom, m.top) != (20, 56535)          # auto intensity moved the thresholds
    rgba = v.image.image
    assert rgba.shape[2] == 4 and rgba[..., 3].any()
    assert len(v.grid_items) > 4                                # rings and bearing lines
    # colormap and manual sliders
    before = rgba.copy()
    w.cmap.setCurrentText("Jet")
    assert not np.array_equal(v.image.image, before)
    w.auto.setChecked(False)
    assert w.sensitivity.isEnabled()
    w.sensitivity.setValue(500)
    w.intensity.setValue(30000)
    assert (m.bottom, m.top) == (500, 66535 - 30000)
    assert w.settings.value("colormap") == "Jet"
    # gain/TVG reach the sonar, smoothing reaches the service
    w.gain.setValue(3.0)
    w.gain.editingFinished.emit()
    w.smooth.setChecked(False)
    assert w.c.status()["smooth"] is False
    w.btn_stop.click()
    assert w.c.status()["running"] is False
    emu.stop.set()


@needs_file
def test_file_playback_at_recorded_pace(qtbot, window):
    w = window
    w.connect_to(FILE)                                          # the same as Open .son…
    assert w.info["live"] is False
    assert not w.range_stop.isEnabled()                         # a file cannot change range
    w.btn_start.click()
    _wait_image(qtbot, w.views[0])
    assert w.c.status()["speed"] == 1.0                         # plays at the recorded pace
    n0 = w.received
    qtbot.wait(500)
    assert 0 < w.received - n0 < 40                             # ~19 pings/s recorded, not as fast as possible


@needs_file
def test_discover_lists_model_serial_and_ip(qtbot, window):
    try:
        t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        t.bind(("", 1149))
        t.close()
    except OSError:
        pytest.skip("UDP port 1149 busy")
    emu = _emulator(discovery=True)
    time.sleep(0.3)
    w = window
    w.btn_discover.click()
    rows = [[w.found.item(r, k).text() for k in range(3)] for r in range(w.found.rowCount())]
    mine = [r for r in rows if r[2].endswith(f":{emu.tcp_port}")]          # a real sonar may answer too
    assert mine and mine[0][:2] == ["P900-130", "2058"]
    w.found.selectRow(rows.index(mine[0]))
    w.btn_connect_sel.click()
    assert w.info["serial"] == "2058"
    emu.stop.set()


DUAL = os.path.join(ROOT, "samples", "live", "server_h0h3.son")     # real M900/2250: pings on heads 0 and 3


@pytest.mark.skipif(not os.path.exists(DUAL), reason="no multi-head recording")
def test_two_heads_side_by_side(qtbot, window):
    w = window
    w.connect_to(DUAL)
    assert w.heads.count() == 6 and list(w.views) == [0]
    w.heads.item(3).setCheckState(QtCore.Qt.Checked)            # tick the 900 kHz head
    assert sorted(w.views) == [0, 3]
    pos = [w.panel_layout.getItemPosition(w.panel_layout.indexOf(w.views[h]))[:2] for h in (0, 3)]
    assert pos == [(0, 0), (0, 1)]                              # side by side
    w.btn_start.click()
    for h in (0, 3):
        _wait_image(qtbot, w.views[h])
    assert w.c.status()["heads"] == [0, 3]
    w.heads.item(0).setCheckState(QtCore.Qt.Unchecked)          # while running: only head 3
    assert list(w.views) == [3] and w.c.status()["heads"] == [3]


# ---------------------------------------------------------------- tools, recording, playback, status, settings

def _live(qtbot, w, emu):
    w.ip.setText(f"127.0.0.1:{emu.tcp_port}")
    w.btn_connect_ip.click()
    w.btn_start.click()
    _wait_image(qtbot, w.views[0])
    return w.views[0]


@needs_file
def test_cursor_ruler_and_saving(qtbot, window, tmp_path):
    from PIL import Image
    from opensonar.gui import polar
    emu = _emulator()
    w = window
    v = _live(qtbot, w, emu)
    assert polar(3, 4) == (5.0, pytest.approx(36.8699, abs=1e-3))
    w._cursor(v, -1.0, 2.0)
    assert "range 2.24 m" in w.status_cursor.text() and "bearing -26.6°" in w.status_cursor.text()
    w.act_ruler.setChecked(True)
    assert v.ruler_on
    v.ruler_click(0.0, 1.0)
    v.ruler_click(3.0, 5.0)
    assert v.ruler_distance == pytest.approx(5.0)
    v.ruler_click(1.0, 1.0)                                     # a third click starts again
    assert v.ruler_distance is None and len(v.ruler_points) == 1
    w.act_ruler.setChecked(False)
    assert v.ruler_points == []
    png = w.save_screen(str(tmp_path / "screen.png"))
    assert Image.open(png).size[0] > 100
    [tif] = w.save_tiff(str(tmp_path / "fan.tif"))
    a = np.asarray(Image.open(tif))
    assert a.dtype == np.uint16 and np.array_equal(a, v.last[0])
    emu.stop.set()


@needs_file
def test_recording_with_indicator_and_marks(qtbot, window, tmp_path):
    import csv
    import opensonar
    emu = _emulator()
    w = window
    _live(qtbot, w, emu)
    path = str(tmp_path / "session")                           # .son is added
    assert w.start_recording(path)
    qtbot.wait(1200)
    w.refresh_status()
    text = w.status_rec.text()
    assert text.startswith("● REC 00:00:01") and "pings" in text and ("kB" in text or "MB" in text)
    w.add_mark("buoy")
    r = w.stop_recording()
    assert not w.act_record.isChecked() and w.status_rec.text() == ""
    assert r["path"].endswith("session.son") and r["marks"] == 1
    assert opensonar.open(r["path"]).head(0).n_pings == r["pings"]
    assert [x["text"] for x in csv.DictReader(open(r["events"]))] == ["buoy"]
    # status bar while live
    w.refresh_status()
    assert w.status_lost.text() == "lost 0"
    assert w.status_utc.text().startswith("UTC ")
    assert w.status_clock.text().startswith("sonar clock ")
    emu.stop.set()


@needs_file
def test_playback_bar(qtbot, window):
    import opensonar
    w = window
    w.connect_to(FILE)
    assert w.playback.isVisible() or not w.isVisible()          # shown for files
    assert w.playback.isVisibleTo(w)
    orig = opensonar.open(FILE).head(0)
    v = w.views[0]
    w.goto(100)
    qtbot.waitUntil(lambda: v.last is not None and v.last[2].ping.number == orig.ping(100).number, timeout=10000)
    w.step(1)
    qtbot.waitUntil(lambda: v.last[2].ping.number == orig.ping(101).number, timeout=10000)
    w.step(-1)
    qtbot.waitUntil(lambda: v.last[2].ping.number == orig.ping(100).number, timeout=10000)
    w.refresh_status()
    assert w.timeline.value() == 100 and w.time_label.text() == "101 / 806"
    w.speed.setCurrentIndex(3)                                   # 2×
    w.btn_play.click()                                           # play
    assert w.btn_play.isChecked() and w.c.status()["speed"] == 2.0
    qtbot.wait(600)
    w.btn_play.click()                                           # pause
    assert not w.c.status()["running"]
    w.refresh_status()
    assert w.timeline.value() > 101


@needs_file
def test_export_range_in_several_formats(window, tmp_path):
    import csv
    from PIL import Image
    import opensonar
    w = window
    w.connect_to(FILE)
    assert w.act_export.isEnabled()
    n = w.export_pings(0, 10, 12, ["tiff", "npy", "mp4"], "xy", str(tmp_path))
    assert n == 3
    tifs = sorted(os.listdir(tmp_path / "exp1_tiff" / "h0"))
    assert tifs == ["index.csv", "ping_000000.tif", "ping_000001.tif", "ping_000002.tif"]
    h = opensonar.open(FILE).head(0)
    ref = h.image(h.ping(10), crop=True)[0]
    assert np.array_equal(np.asarray(Image.open(tmp_path / "exp1_tiff" / "h0" / "ping_000000.tif")), ref)
    assert np.array_equal(np.load(tmp_path / "exp1_npy" / "h0" / "ping_000000.npy"), ref)
    rows = list(csv.DictReader(open(tmp_path / "exp1_npy" / "h0" / "index.csv")))
    assert [int(r["number"]) for r in rows] == [h.ping(i).number for i in (10, 11, 12)]
    assert rows[0]["utc"].endswith("Z") and float(rows[0]["res"]) > 0
    assert os.path.getsize(tmp_path / "exp1_h0_xy.mp4") > 1000


@needs_file
def test_record_in_several_formats(qtbot, window, tmp_path):
    import csv
    import opensonar
    emu = _emulator()
    w = window
    _live(qtbot, w, emu)
    assert w.start_recording(str(tmp_path / "multi"), ["son", "npy", "png", "mp4"])
    qtbot.wait(1500)
    w.refresh_status()
    assert "video" in w.status_rec.text() and "pings" in w.status_rec.text()
    r = w.stop_recording()
    n = r["pings"]
    assert n > 3 and r["formats"] == ["son", "npy", "png"] and r["images"] == n          # pings rendered (one file per format each)
    assert opensonar.open(str(tmp_path / "multi.son")).head(0).n_pings == n
    for fmt, ext in (("npy", "npy"), ("png", "png")):
        files = sorted(f for f in os.listdir(tmp_path / f"multi_{fmt}" / "h0") if f.endswith(ext))
        assert len(files) == n                                          # every ping, none skipped
        assert len(list(csv.DictReader(open(tmp_path / f"multi_{fmt}" / "h0" / "index.csv")))) == n
    assert r["video_frames"] >= 10 and os.path.getsize(tmp_path / "multi.mp4") > 1000
    emu.stop.set()


def test_settings_are_remembered(qtbot, server, tmp_path):
    ini = str(tmp_path / "gui.ini")

    def make():
        s = QtCore.QSettings(ini, QtCore.QSettings.IniFormat)
        w = build(Service(rep_port=server.rep_port, pub_port=server.pub_port, spawn=False), s)
        qtbot.addWidget(w)
        return w
    w = make()
    assert w.cmap.currentText() == "Copper" and w.auto.isChecked()
    assert list(w.maps)[:4] == ["Copper", "Grayscale", "Jet", "Viridis"]
    w.cmap.setCurrentText("Viridis")
    w.auto.setChecked(False)
    w.sensitivity.setValue(123)
    w.intensity.setValue(23456)
    w.gamma.setValue(0.8)
    w.grid.setChecked(False)
    w.speed.setCurrentIndex(4)
    w.ip.setText("10.0.0.7")
    w.settings.setValue("last_ip", "10.0.0.7")
    w.resize(700, 500)
    w.close()
    w2 = make()
    assert (w2.cmap.currentText(), w2.auto.isChecked(), w2.sensitivity.value(), w2.intensity.value()) == \
        ("Viridis", False, 123, 23456)
    assert w2.gamma.value() == pytest.approx(0.8) and not w2.grid.isChecked() and w2.speed.currentIndex() == 4
    assert w2.ip.text() == "10.0.0.7" and w2.size().width() == 700
    w2.close()


@pytest.mark.skipif(not os.path.exists(DUAL), reason="no multi-head recording")
def test_ticked_heads_are_remembered(qtbot, window):
    w = window
    w.connect_to(DUAL)
    w.heads.item(3).setCheckState(QtCore.Qt.Checked)
    w.heads.item(0).setCheckState(QtCore.Qt.Unchecked)
    w.disconnect()
    w.connect_to(DUAL)
    assert w.selected_heads() == [3]


def _aris_file(d):
    sys.path.insert(0, os.path.dirname(__file__))
    from test_aris import write_aris
    path = os.path.join(d, "t.aris")
    rng = np.random.default_rng(3)
    write_aris(path, [rng.integers(0, 256, (200, 48), dtype=np.uint8) for _ in range(5)], system_type=2)
    return path


def test_aris_file_playback(qtbot, window):
    w = window
    with tempfile.TemporaryDirectory() as d:
        w.connect_to(_aris_file(d))
        assert w.info["live"] is False and w.info["sonar_type"] == "aris"
        assert w.heads.count() == 1 and "kHz" not in w.heads.item(0).text()
        w.btn_start.click()
        img = _wait_image(qtbot, w.views[0])
        assert img is not None
        w.btn_stop.click()
        w.disconnect()


def test_aris_live_by_ip_and_family(qtbot, window):
    from aris_emulator import Emulator
    from opensonar.aris_net import COMMAND_PORT
    w = window
    with tempfile.TemporaryDirectory() as d:
        emu = Emulator(_aris_file(d), frame_rate=20)
        try:
            srv = socket.create_server(("127.0.0.1", COMMAND_PORT))
            srv.close()
        except OSError:
            pytest.skip(f"TCP port {COMMAND_PORT} busy")
        threading.Thread(target=emu.serve_tcp, args=(COMMAND_PORT, "127.0.0.1"), daemon=True).start()
        while emu.tcp_port is None:
            time.sleep(0.01)
        try:
            w.ip.setText("127.0.0.1")
            w.family.setCurrentIndex(w.family.findData("aris"))
            # without a beacon the sonar type is unknown: the GUI passes the one from discovery
            w.connect_ip(system_type=2)
            assert w.info["live"] is True and w.info["model"] == "1200"
            assert w.range_stop.isEnabled() and not w.tvg.isEnabled()      # no TVG on these sonars
            assert w.settings.value("last_family") == "aris"
            w.btn_start.click()
            assert _wait_image(qtbot, w.views[0]) is not None
            w.btn_stop.click()
            w.disconnect()
        finally:
            emu.stop()
