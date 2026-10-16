"""opensonar GUI (PySide6 + pyqtgraph), a client of the opensonar service (docs/SERVER.md).

On start it connects to the service and, if none is running, starts one itself (`python -m opensonar
server`); on close it stops the service only if it started it. Display as in docs/DISPLAY.md; user
guide in docs/GUI.md.
"""
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import numpy as np

from . import display
from .client import Client, ServerError, Subscriber
from .server import PUB_PORT, REP_PORT

ORG, APP = "opensonar", "opensonar-gui"
SPEEDS = (("0.25×", 0.25), ("0.5×", 0.5), ("1×", 1.0), ("2×", 2.0), ("4×", 4.0), ("max", None))


def utc_now_name():
    return datetime.now(timezone.utc).strftime("opensonar_%Y%m%d_%H%M%SZ")


def human_bytes(n):
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000.0


def hms(seconds):
    s = int(seconds)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def polar(x, y):
    """(range m, bearing deg) of a point in the fan; bearing 0 = straight ahead, positive to the right."""
    return math.hypot(x, y), math.degrees(math.atan2(x, y))


FORMAT_LABELS = (("son", "raw (.son, or .aris for those sonars)"), ("mp4", "MP4 video (as on screen)"), ("npy", "NPY (u16 per ping)"),
                 ("npz", "NPZ (u16 + metadata)"), ("png", "PNG 16 bit"), ("tiff", "TIFF 16 bit"))


def qimage_rgb(qimage):
    """QImage → (rows, cols, 3) uint8."""
    from PySide6 import QtGui
    img = qimage.convertToFormat(QtGui.QImage.Format_RGB888)
    w, h = img.width(), img.height()
    a = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * h).reshape(h, img.bytesPerLine())
    return a[:, :w * 3].reshape(h, w, 3).copy()


# ---------------------------------------------------------------- service

class Service:
    """Finds the opensonar service, or starts it as a child process."""

    def __init__(self, host="127.0.0.1", rep_port=REP_PORT, pub_port=PUB_PORT, spawn=True, wait=15.0):
        self.rep = f"tcp://{host}:{rep_port}"
        self.pub = f"tcp://{host}:{pub_port}"
        self.process = None
        self.client = Client(self.rep, timeout=1.0)
        if not self._alive():
            if not spawn:
                raise ServerError(f"no opensonar service at {self.rep}")
            self.process = subprocess.Popen(
                [sys.executable, "-m", "opensonar", "server", "--bind", host,
                 "--rep-port", str(rep_port), "--pub-port", str(pub_port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            end = time.time() + wait
            while not self._alive():
                if self.process.poll() is not None or time.time() > end:
                    self.stop()
                    raise ServerError("could not start the opensonar service")
                time.sleep(0.1)
        self.client.timeout = 30.0                    # discovery with probing can take a few seconds

    @property
    def owned(self):
        return self.process is not None

    def _alive(self):
        try:
            self.client.status()
            return True
        except ServerError:
            return False

    def stop(self):
        """Stops the service if this object started it (closes any recording first)."""
        if self.process is None:
            self.client.close()
            return
        try:
            self.client.timeout = 5.0
            self.client.quit()
        except ServerError:
            pass
        self.client.close()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None


# ---------------------------------------------------------------- widgets

def _qt():
    from PySide6 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def build(service=None, settings=None):
    """Creates the main window (imports Qt lazily, so `import opensonar.gui` works without PySide6)."""
    QtCore, QtGui, QtWidgets = _qt()
    import pyqtgraph as pg
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)
    GREEN = (60, 200, 120)

    class SonarView(pg.PlotWidget):
        """One head: the colour-mapped fan in metres, with range rings, bearing lines, cursor and ruler."""

        def __init__(self, head, title, on_cursor=None):
            super().__init__(background="k")
            self.head = head
            self.on_cursor = on_cursor
            self.setAspectLocked(True)
            self.setLabel("bottom", "x", "m")
            self.setLabel("left", "y", "m")
            self.setTitle(title, color="#ccc", size="10pt")
            self.image = pg.ImageItem()
            self.addItem(self.image)
            self.grid_items = []
            self.grid_on = True
            self.last = None                     # (u16 image, image meta, ping message) on screen
            self._grid_key = None
            self._fov = None
            # ruler: two clicks; a third click starts again
            self.ruler_on = False
            self.ruler_points = []
            self.ruler_distance = None
            self._ruler_line = pg.PlotDataItem(pen=pg.mkPen((255, 220, 0), width=2), symbol="o", symbolSize=6,
                                               symbolBrush=(255, 220, 0))
            self._ruler_text = pg.TextItem(color=(255, 220, 0), anchor=(0, 1))
            self.addItem(self._ruler_line)
            self.addItem(self._ruler_text)
            self.scene().sigMouseMoved.connect(self._moved)
            self.scene().sigMouseClicked.connect(self._clicked)

        # --- image ---
        def show_ping(self, msg, mapper):
            img, im = display.crop_fan(msg.image, msg.meta["image"], msg.ping.range_stop)
            self.last = (img, im, msg)
            self.redraw(mapper)
            x0, x1, y0, y1 = display.fan_extent(im)
            key = (round(msg.ping.range_stop, 2), self.grid_on, img.shape)
            if key != self._grid_key:
                self._grid_key = key
                self._fov = self._fan_angle(img, im)
                self.draw_grid(msg.ping.range_stop)
                self.setRange(xRange=(x0, x1), yRange=(0, y1), padding=0.02)

        def redraw(self, mapper):
            if self.last is None:
                return
            img, im, _ = self.last
            self.image.setImage(mapper.map(img)[::-1], autoLevels=False)       # row 0 = farthest → top
            x0, x1, y0, y1 = display.fan_extent(im)
            self.image.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))

        @staticmethod
        def _fan_angle(img, im):
            r, c = np.nonzero(img[::4, ::4])
            if r.size == 0:
                return math.radians(65)
            x = (c * 4 - im["origin_col"]) * im["res"]
            y = (im["origin_row"] - r * 4) * im["res"]
            return float(np.max(np.abs(np.arctan2(x, np.maximum(y, 1e-6)))))

        def draw_grid(self, radius):
            for it in self.grid_items:
                self.removeItem(it)
            self.grid_items = []
            if not self.grid_on or radius <= 0:
                return
            pen = pg.mkPen(GREEN + (150,), width=1)
            step = next(p for p in (0.25, 0.5, 1, 2, 5, 10, 20, 25, 50) if radius / p <= 8)
            lim = self._fov or math.radians(65)
            a = np.linspace(-lim, lim, 121)
            for r in np.arange(step, radius + 1e-9, step):
                ring = pg.PlotCurveItem(r * np.sin(a), r * np.cos(a), pen=pen)
                label = pg.TextItem(f"{r:g} m", color=GREEN, anchor=(0.5, 1))
                label.setPos(0, r)
                self.grid_items += [ring, label]
            deg = 15
            for k in range(-int(math.degrees(lim) // deg), int(math.degrees(lim) // deg) + 1):
                t = math.radians(k * deg)
                ray = pg.PlotCurveItem([0, radius * math.sin(t)], [0, radius * math.cos(t)], pen=pen)
                label = pg.TextItem(f"{k * deg}°", color=GREEN, anchor=(0.5, 1))
                label.setPos(1.04 * radius * math.sin(t), 1.04 * radius * math.cos(t))
                self.grid_items += [ray, label]
            for it in self.grid_items:
                self.addItem(it)

        # --- cursor and ruler ---
        def to_metres(self, scene_pos):
            p = self.getPlotItem().vb.mapSceneToView(scene_pos)
            return p.x(), p.y()

        def _moved(self, pos):
            if self.on_cursor is not None and self.sceneBoundingRect().contains(pos):
                self.on_cursor(self, *self.to_metres(pos))

        def _clicked(self, ev):
            if self.ruler_on and ev.button() == QtCore.Qt.LeftButton:
                self.ruler_click(*self.to_metres(ev.scenePos()))
                ev.accept()

        def ruler_click(self, x, y):
            if len(self.ruler_points) >= 2:
                self.ruler_points = []
            self.ruler_points.append((x, y))
            xs, ys = zip(*self.ruler_points)
            self._ruler_line.setData(list(xs), list(ys))
            if len(self.ruler_points) == 2:
                (xa, ya), (xb, yb) = self.ruler_points
                self.ruler_distance = math.hypot(xb - xa, yb - ya)
                self._ruler_text.setText(f"{self.ruler_distance:.2f} m")
                self._ruler_text.setPos((xa + xb) / 2, (ya + yb) / 2)
            else:
                self.ruler_distance = None
                self._ruler_text.setText("")

        def clear_ruler(self):
            self.ruler_points, self.ruler_distance = [], None
            self._ruler_line.setData([], [])
            self._ruler_text.setText("")

    def format_boxes(form, chosen, available):
        boxes = {}
        grid = QtWidgets.QGridLayout()
        for k, (fmt, label) in enumerate([x for x in FORMAT_LABELS if x[0] in available]):
            b = QtWidgets.QCheckBox(label)
            b.setChecked(fmt in chosen)
            boxes[fmt] = b
            grid.addWidget(b, k // 2, k % 2)
        form.addRow("Formats", grid)
        return boxes

    class RecordDialog(QtWidgets.QDialog):
        """Where and in which formats to record (any combination)."""

        def __init__(self, parent, folder, name, formats):
            super().__init__(parent)
            self.setWindowTitle("Record")
            form = QtWidgets.QFormLayout(self)
            row = QtWidgets.QHBoxLayout()
            self.folder = QtWidgets.QLineEdit(folder)
            b = QtWidgets.QPushButton("…")
            b.clicked.connect(self._choose)
            row.addWidget(self.folder, 1)
            row.addWidget(b)
            form.addRow("Folder", row)
            self.name = QtWidgets.QLineEdit(name)
            form.addRow("Name", self.name)
            self.boxes = format_boxes(form, formats, [f for f, _ in FORMAT_LABELS])
            note = QtWidgets.QLabel(".son and the per-ping images are recorded by the service (every ping); "
                                    "the video records the panels as shown.")
            note.setWordWrap(True)
            form.addRow(note)
            box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            box.accepted.connect(self.accept)
            box.rejected.connect(self.reject)
            form.addRow(box)

        def _choose(self):
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Record to", self.folder.text())
            if d:
                self.folder.setText(d)

        def values(self):
            name = self.name.text().strip() or utc_now_name()
            if name.lower().endswith(".son"):
                name = name[:-4]
            return os.path.join(self.folder.text(), name), [f for f, b in self.boxes.items() if b.isChecked()]

    class ExportDialog(QtWidgets.QDialog):
        """Export a range of pings of a .son as PNG, TIFF or NPZ."""

        def __init__(self, parent, path, heads, n_pings, out_dir, formats=("png",)):
            super().__init__(parent)
            self.setWindowTitle("Export pings")
            form = QtWidgets.QFormLayout(self)
            self.path = QtWidgets.QLabel(path)
            form.addRow("File", self.path)
            self.head = QtWidgets.QComboBox()
            for i, text in heads:
                self.head.addItem(text, i)
            form.addRow("Head", self.head)
            self.n_pings = n_pings
            self.first, self.last = QtWidgets.QSpinBox(), QtWidgets.QSpinBox()
            row = QtWidgets.QHBoxLayout()
            row.addWidget(self.first)
            row.addWidget(QtWidgets.QLabel("to"))
            row.addWidget(self.last)
            form.addRow("Pings", row)
            self.head.currentIndexChanged.connect(self._limits)
            self._limits()
            self.boxes = format_boxes(form, formats, ["mp4", "npy", "npz", "png", "tiff"])
            self.kind = QtWidgets.QComboBox()
            self.kind.addItems(["xy", "rtheta"])
            form.addRow("Image", self.kind)
            row = QtWidgets.QHBoxLayout()
            self.out = QtWidgets.QLineEdit(out_dir)
            b = QtWidgets.QPushButton("…")
            b.clicked.connect(self._choose)
            row.addWidget(self.out, 1)
            row.addWidget(b)
            form.addRow("Folder", row)
            box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            box.accepted.connect(self.accept)
            box.rejected.connect(self.reject)
            form.addRow(box)

        def _limits(self, *_):
            n = max(self.n_pings.get(self.head.currentData(), 0), 1)
            for s in (self.first, self.last):
                s.setRange(0, n - 1)
            self.last.setValue(n - 1)

        def _choose(self):
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Export to", self.out.text())
            if d:
                self.out.setText(d)

        def values(self):
            return dict(head=self.head.currentData(), first=self.first.value(), last=self.last.value(),
                        formats=[f for f, b in self.boxes.items() if b.isChecked()], kind=self.kind.currentText(),
                        out_dir=self.out.text())

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self, service, settings):
            super().__init__()
            self.service = service
            self.c = service.client
            self.settings = settings or QtCore.QSettings(ORG, APP)
            self.setWindowTitle("opensonar")
            self.resize(1400, 850)
            self.info = None                     # reply of `heads`
            self.views = {}                      # head → SonarView
            self.mappers = {}                    # head → ColorMapper
            self.cmap_folders = [d for d in [self.settings.value("cmap_folder", "")] if d]
            self.maps = display.colormaps(self.cmap_folders)
            self.sub = None
            self.received, self.lost, self._last_seq, self._last_msg = 0, 0, None, None
            self._rate = []
            self.status = {}
            self.recording, self.video = None, None      # (base path, formats), MP4 writer
            self._restored = False
            self._build()
            self._restore()
            self._restored = True
            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self.poll)
            self.timer.start(15)
            self.status_timer = QtCore.QTimer(self)
            self.status_timer.timeout.connect(self.refresh_status)
            self.status_timer.start(250)
            self._set_connected(False)

        # --- layout ---
        def _build(self):
            W = QtWidgets
            centre = W.QWidget()
            vbox = W.QVBoxLayout(centre)
            vbox.setContentsMargins(0, 0, 0, 0)
            self.panels = W.QWidget()
            self.panel_layout = W.QGridLayout(self.panels)
            self.placeholder = W.QLabel("Discover or type a sonar IP, or open a .son or .aris file, then press Start.")
            self.placeholder.setAlignment(QtCore.Qt.AlignCenter)
            self.panel_layout.addWidget(self.placeholder, 0, 0)
            vbox.addWidget(self.panels, 1)
            vbox.addWidget(self._build_playback())
            self.setCentralWidget(centre)
            self._build_toolbar()

            side = W.QWidget()
            col = W.QVBoxLayout(side)

            # connection
            g = W.QGroupBox("Connection")
            lay = W.QVBoxLayout(g)
            self.btn_network = W.QPushButton("Configure sonar network")
            self.btn_network.setToolTip("Activates the opensonar-net profile (192.168.1.10/24 on the wired card)")
            self.btn_network.clicked.connect(self.configure_network)
            lay.addWidget(self.btn_network)
            self.btn_discover = W.QPushButton("Discover sonars")
            self.btn_discover.clicked.connect(self.discover)
            self.found = W.QTableWidget(0, 3)
            self.found.setHorizontalHeaderLabels(["Model", "Serial", "IP"])
            self.found.horizontalHeader().setStretchLastSection(True)
            self.found.setSelectionBehavior(W.QAbstractItemView.SelectRows)
            self.found.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
            self.found.setMaximumHeight(110)
            self.found.itemDoubleClicked.connect(lambda *_: self.connect_selected())
            self.btn_connect_sel = W.QPushButton("Connect to selected")
            self.btn_connect_sel.clicked.connect(self.connect_selected)
            row = W.QHBoxLayout()
            self.ip = W.QLineEdit(self.settings.value("last_ip", "192.168.1.45"))
            self.ip.setPlaceholderText("IP or ip:port")
            self.ip.returnPressed.connect(lambda: self.connect_ip())
            self.family = W.QComboBox()
            self.family.addItem("P900 / M900", "")
            self.family.addItem(".aris sonar", "aris")
            self.family.setCurrentIndex(max(0, self.family.findData(self.settings.value("last_family", ""))))
            self.btn_connect_ip = W.QPushButton("Connect")
            self.btn_connect_ip.clicked.connect(lambda: self.connect_ip())
            row.addWidget(self.ip)
            row.addWidget(self.family)
            row.addWidget(self.btn_connect_ip)
            self.btn_open = W.QPushButton("Open recording…")
            self.btn_open.clicked.connect(self.open_file)
            self.btn_disconnect = W.QPushButton("Disconnect")
            self.btn_disconnect.clicked.connect(self.disconnect)
            for w in (self.btn_discover, self.found, self.btn_connect_sel):
                lay.addWidget(w)
            lay.addLayout(row)
            lay.addWidget(self.btn_open)
            lay.addWidget(self.btn_disconnect)
            col.addWidget(g)

            # heads
            g = W.QGroupBox("Heads (tick to show)")
            lay = W.QVBoxLayout(g)
            self.heads = W.QListWidget()
            self.heads.setMaximumHeight(130)
            self.heads.itemChanged.connect(self._heads_changed)
            lay.addWidget(self.heads)
            col.addWidget(g)

            # acquisition
            g = W.QGroupBox("Acquisition")
            form = W.QFormLayout(g)
            self.ctl_head = W.QComboBox()
            self.ctl_head.currentIndexChanged.connect(self._control_head_changed)
            form.addRow("Head", self.ctl_head)
            self.range_slider = W.QSlider(QtCore.Qt.Horizontal)
            self.range_slider.sliderReleased.connect(self._slider_released)
            self.range_slider.valueChanged.connect(
                lambda v: self.range_stop.setValue(v / 10.0) if self.range_slider.isSliderDown() else None)
            form.addRow("Range", self.range_slider)
            self.range_start, self.range_stop = W.QDoubleSpinBox(), W.QDoubleSpinBox()
            for s in (self.range_start, self.range_stop):
                s.setDecimals(2)
                s.setSuffix(" m")
                s.setKeyboardTracking(False)
                s.editingFinished.connect(self.apply_range)
            row = W.QHBoxLayout()
            row.addWidget(self.range_start)
            row.addWidget(W.QLabel("to"))
            row.addWidget(self.range_stop)
            form.addRow("Start / stop", row)
            self.gain, self.tvg = W.QDoubleSpinBox(), W.QDoubleSpinBox()
            for s, name in ((self.gain, "gain"), (self.tvg, "tvg")):
                s.setRange(0, 100)
                s.setDecimals(2)
                s.setKeyboardTracking(False)
                s.editingFinished.connect(lambda name=name: self.apply_setting(name))
            form.addRow("Gain", self.gain)
            form.addRow("TVG", self.tvg)
            row = W.QHBoxLayout()
            self.btn_start = W.QPushButton("Start")
            self.btn_start.clicked.connect(self.start)
            self.btn_stop = W.QPushButton("Stop")
            self.btn_stop.clicked.connect(self.stop)
            row.addWidget(self.btn_start)
            row.addWidget(self.btn_stop)
            form.addRow(row)
            col.addWidget(g)

            # display
            g = W.QGroupBox("Display")
            form = W.QFormLayout(g)
            self.cmap = W.QComboBox()
            self.cmap.addItems(list(self.maps))
            self.cmap.setCurrentText(display.DEFAULTS["colormap"])
            self.cmap.currentTextChanged.connect(self._display_changed)
            row = W.QHBoxLayout()
            row.addWidget(self.cmap, 1)
            b = W.QPushButton(".cmap folder…")
            b.clicked.connect(self.choose_cmap_folder)
            row.addWidget(b)
            form.addRow("Colormap", row)
            self.auto = W.QCheckBox("Auto Intensity")
            self.auto.setChecked(display.DEFAULTS["auto"])
            self.auto.toggled.connect(self._display_changed)
            form.addRow(self.auto)
            self.sensitivity = W.QSlider(QtCore.Qt.Horizontal)
            self.sensitivity.setRange(*display.SENSITIVITY_RANGE)
            self.sensitivity.setValue(display.DEFAULTS["sensitivity"])
            self.intensity = W.QSlider(QtCore.Qt.Horizontal)
            self.intensity.setRange(*display.INTENSITY_RANGE)
            self.intensity.setValue(display.DEFAULTS["intensity"])
            self.gamma = W.QDoubleSpinBox()
            self.gamma.setRange(0.05, 5.0)
            self.gamma.setSingleStep(0.05)
            self.gamma.setValue(display.DEFAULTS["gamma"])
            for w in (self.sensitivity, self.intensity):
                w.valueChanged.connect(self._display_changed)
            self.gamma.valueChanged.connect(self._display_changed)
            form.addRow("Sensitivity", self.sensitivity)
            form.addRow("Intensity", self.intensity)
            form.addRow("Gamma", self.gamma)
            self.smooth = W.QCheckBox("Smoothing (3×3)")
            self.smooth.setChecked(True)
            self.smooth.toggled.connect(self._smooth_changed)
            self.grid = W.QCheckBox("Range rings and bearings")
            self.grid.setChecked(True)
            self.grid.toggled.connect(self._grid_changed)
            form.addRow(self.smooth)
            form.addRow(self.grid)
            col.addWidget(g)
            col.addStretch(1)

            scroll = W.QScrollArea()
            scroll.setWidget(side)
            scroll.setWidgetResizable(True)
            scroll.setMinimumWidth(340)
            dock = W.QDockWidget("Controls")
            dock.setObjectName("controls")
            dock.setWidget(scroll)
            dock.setFeatures(W.QDockWidget.DockWidgetMovable)
            self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)

            # status bar: connection · cursor · recording · rate · lost · UTC · clock offset
            self.status_conn = W.QLabel()
            self.status_cursor = W.QLabel()
            self.status_rec = W.QLabel()
            self.status_rec.setStyleSheet("color: #e33; font-weight: bold")
            self.status_rate = W.QLabel()
            self.status_lost = W.QLabel()
            self.status_utc = W.QLabel()
            self.status_clock = W.QLabel()
            self.statusBar().addWidget(self.status_conn, 1)
            self.statusBar().addWidget(self.status_cursor)
            for w in (self.status_rec, self.status_rate, self.status_lost, self.status_utc, self.status_clock):
                self.statusBar().addPermanentWidget(w)
            self._manual_widgets = (self.sensitivity, self.intensity, self.gamma)
            self._display_changed()

        def _build_toolbar(self):
            tb = self.addToolBar("Tools")
            tb.setObjectName("tools")
            self.act_record = QtGui.QAction("● Record", self, checkable=True)
            self.act_record.setToolTip("Record every ping to a .son file (in the service)")
            self.act_record.triggered.connect(self._record_clicked)
            self.act_mark = QtGui.QAction("⚑ Mark", self, shortcut="M")
            self.act_mark.setToolTip("Event mark with UTC time and text (M)")
            self.act_mark.triggered.connect(self._mark_clicked)
            self.act_ruler = QtGui.QAction("📏 Ruler", self, checkable=True, shortcut="R")
            self.act_ruler.setToolTip("Distance between two clicks (R)")
            self.act_ruler.toggled.connect(self._ruler_toggled)
            self.act_png = QtGui.QAction("Save screen (PNG)", self)
            self.act_png.triggered.connect(self._save_screen_clicked)
            self.act_tiff = QtGui.QAction("Save image (TIFF 16 bit)", self)
            self.act_tiff.triggered.connect(self._save_tiff_clicked)
            self.act_export = QtGui.QAction("Export pings…", self)
            self.act_export.triggered.connect(self._export_clicked)
            for a in (self.act_record, self.act_mark, None, self.act_ruler, None, self.act_png, self.act_tiff,
                      self.act_export):
                tb.addSeparator() if a is None else tb.addAction(a)

        def _build_playback(self):
            W = QtWidgets
            bar = W.QWidget()
            row = W.QHBoxLayout(bar)
            row.setContentsMargins(6, 2, 6, 2)
            self.btn_back = W.QPushButton("⏮")
            self.btn_back.setToolTip("Previous ping")
            self.btn_back.clicked.connect(lambda: self.step(-1))
            self.btn_play = W.QPushButton("▶")
            self.btn_play.setCheckable(True)
            self.btn_play.setToolTip("Play / pause")
            self.btn_play.clicked.connect(self._play_clicked)
            self.btn_fwd = W.QPushButton("⏭")
            self.btn_fwd.setToolTip("Next ping")
            self.btn_fwd.clicked.connect(lambda: self.step(1))
            self.timeline = W.QSlider(QtCore.Qt.Horizontal)
            self.timeline.sliderReleased.connect(lambda: self.goto(self.timeline.value()))
            self.time_label = W.QLabel()
            self.speed = W.QComboBox()
            for text, v in SPEEDS:
                self.speed.addItem(text, v)
            self.speed.setCurrentIndex(2)
            self.speed.currentIndexChanged.connect(self._speed_changed)
            self.goto_box = W.QSpinBox()
            self.goto_box.setPrefix("ping ")
            self.goto_box.setKeyboardTracking(False)
            self.btn_goto = W.QPushButton("Go")
            self.btn_goto.clicked.connect(lambda: self.goto(self.goto_box.value()))
            for w in (self.btn_back, self.btn_play, self.btn_fwd):
                row.addWidget(w)
            row.addWidget(self.timeline, 1)
            row.addWidget(self.time_label)
            row.addWidget(W.QLabel("Speed"))
            row.addWidget(self.speed)
            row.addWidget(self.goto_box)
            row.addWidget(self.btn_goto)
            self.playback = bar
            bar.setVisible(False)
            return bar

        # --- settings ---
        def _restore(self):
            st = self.settings
            g = st.value("geometry")
            if g is not None:
                self.restoreGeometry(g)
            s = st.value("window_state")
            if s is not None:
                self.restoreState(s)
            if st.value("colormap") in self.maps:
                self.cmap.setCurrentText(st.value("colormap"))
            b = lambda k, d: str(st.value(k, d)).lower() in ("true", "1")   # noqa: E731 — INI stores text
            self.auto.setChecked(b("auto", display.DEFAULTS["auto"]))
            self.sensitivity.setValue(int(st.value("sensitivity", display.DEFAULTS["sensitivity"])))
            self.intensity.setValue(int(st.value("intensity", display.DEFAULTS["intensity"])))
            self.gamma.setValue(float(st.value("gamma", display.DEFAULTS["gamma"])))
            self.smooth.setChecked(b("smooth", True))
            self.grid.setChecked(b("grid", True))
            self.speed.setCurrentIndex(int(st.value("speed_index", 2)))

        def save_settings(self):
            st = self.settings
            st.setValue("geometry", self.saveGeometry())
            st.setValue("window_state", self.saveState())
            st.setValue("colormap", self.cmap.currentText())
            st.setValue("auto", self.auto.isChecked())
            st.setValue("sensitivity", self.sensitivity.value())
            st.setValue("intensity", self.intensity.value())
            st.setValue("gamma", self.gamma.value())
            st.setValue("smooth", self.smooth.isChecked())
            st.setValue("grid", self.grid.isChecked())
            st.setValue("speed_index", self.speed.currentIndex())
            if self.info:
                st.setValue(f"heads/{self.info['serial']}", ",".join(map(str, self.selected_heads())))
            st.sync()

        # --- helpers ---
        def _call(self, fn, *a, quiet=False, **kw):
            try:
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
                return fn(*a, **kw)
            except ServerError as e:
                if not quiet:
                    QtWidgets.QMessageBox.warning(self, "opensonar", str(e))
                return None
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()

        def _set_connected(self, on):
            for w in (self.btn_start, self.btn_stop, self.btn_disconnect, self.ctl_head, self.act_record,
                      self.act_mark):
                w.setEnabled(on)
            live = bool(on and self.info and self.info["live"])
            for w in (self.range_slider, self.range_start, self.range_stop, self.gain, self.tvg):
                w.setEnabled(live)
            self.playback.setVisible(bool(on and self.info and not self.info["live"]))
            self.act_export.setEnabled(bool(on and self.info and not self.info["live"]))
            if not on:
                self.info = None
                self.heads.clear()
                self.ctl_head.clear()
                self.status_conn.setText("Not connected" + ("" if self.service.owned else " (external service)"))

        def current_view(self):
            return next(iter(self.views.values()), None)

        # --- connection ---
        def discover(self):
            found = self._call(self.c.discover, 2.0)
            if found is None:
                return
            self.found.setRowCount(0)
            for a in found:
                r = self.found.rowCount()
                self.found.insertRow(r)
                ip = a.get("Host", "")
                port = a.get("Port", "1149")
                family = a.get("Family", "")
                for k, v in enumerate((a.get("Model", "?"), a.get("Serial", "?"),
                                       ip if family or str(port) == "1149" else f"{ip}:{port}")):
                    self.found.setItem(r, k, QtWidgets.QTableWidgetItem(str(v)))
                self.found.item(r, 2).setData(QtCore.Qt.UserRole, (family, a.get("SystemType")))
            if found:
                self.found.selectRow(0)
            self.statusBar().showMessage(f"{len(found)} sonar(s) found", 4000)

        def configure_network(self):
            from . import netsetup
            try:
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
                msg = netsetup.setup()
            except (netsetup.NetSetupError, OSError, subprocess.SubprocessError) as e:
                QtWidgets.QApplication.restoreOverrideCursor()
                QtWidgets.QMessageBox.warning(self, "opensonar", f"Could not configure the sonar network:\n{e}")
                return None
            QtWidgets.QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(msg, 10000)
            return msg

        def connect_selected(self):
            r = self.found.currentRow()
            if r >= 0:
                item = self.found.item(r, 2)
                family, system_type = item.data(QtCore.Qt.UserRole) or ("", None)
                self.ip.setText(item.text())
                self.family.setCurrentIndex(max(0, self.family.findData(family)))
                self.connect_ip(system_type)

        def connect_ip(self, system_type=None):
            text = self.ip.text().strip()
            family = self.family.currentData()
            options = {"family": family} if family else {}
            if family and system_type is not None:
                options["system_type"] = system_type
            if text and self.connect_to(text, **options):
                self.settings.setValue("last_ip", text)
                self.settings.setValue("last_family", family)

        def open_file(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open recording",
                                                            self.settings.value("last_dir", ""),
                                                            "Sonar recordings (*.son *.aris)")
            if path:
                self.settings.setValue("last_dir", os.path.dirname(path))
                self.connect_to(os.path.abspath(path))

        def connect_to(self, source, **options):
            self._close_sub()
            info = self._call(self.c.connect, source, **options)
            if info is None:
                self._set_connected(False)
                return False
            self.info = info
            saved = self.settings.value(f"heads/{info['serial']}", "0")
            ticked = {int(x) for x in str(saved).split(",") if x.strip().isdigit()} or {0}
            self.heads.blockSignals(True)
            self.heads.clear()
            self.ctl_head.clear()
            for h in info["heads"]:
                f = h.get("center_freq") or 0
                text = f"{h['index']}  {h['model']}" + (f"  {f / 1000:g} kHz" if f else "")
                it = QtWidgets.QListWidgetItem(text)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Checked if h["index"] in ticked else QtCore.Qt.Unchecked)
                it.setData(QtCore.Qt.UserRole, h["index"])
                self.heads.addItem(it)
                self.ctl_head.addItem(text, h["index"])
            self.heads.blockSignals(False)
            self._set_connected(True)
            kind = "live" if info["live"] else "file"
            self.status_conn.setText(f"Connected ({kind}): {info['model']}  serial {info['serial']}  —  "
                                     f"{os.path.basename(info['source'])}")
            self._rebuild_views()
            self._control_head_changed()
            self.received, self.lost, self._last_seq = 0, 0, None
            return True

        def disconnect(self):
            self._close_sub()
            if getattr(self, "recording", None):
                self.stop_recording()
            self._call(self.c.disconnect)
            self.act_record.setChecked(False)
            self._set_connected(False)
            self._rebuild_views()

        # --- heads and panels ---
        def selected_heads(self):
            return [self.heads.item(i).data(QtCore.Qt.UserRole) for i in range(self.heads.count())
                    if self.heads.item(i).checkState() == QtCore.Qt.Checked]

        def _heads_changed(self, *_):
            self._rebuild_views()
            if self.info:
                self.settings.setValue(f"heads/{self.info['serial']}", ",".join(map(str, self.selected_heads())))
            if self.sub is not None and self.status.get("running"):     # pinging: switch to the new set
                self.start()

        def _rebuild_views(self):
            for v in self.views.values():
                self.panel_layout.removeWidget(v)
                v.deleteLater()
            self.views = {}
            heads = self.selected_heads() if self.info else []
            self.placeholder.setVisible(not heads)
            n = len(heads)
            cols = 1 if n <= 1 else 2 if n <= 4 else 3
            for k, h in enumerate(heads):
                model = self.info["heads"][h]["model"]
                v = SonarView(h, f"head {h} — {model}", self._cursor)
                v.grid_on = self.grid.isChecked()
                v.ruler_on = self.act_ruler.isChecked()
                self.views[h] = v
                self.panel_layout.addWidget(v, k // cols, k % cols)
                if h not in self.mappers:
                    self.mappers[h] = self._new_mapper()

        # --- acquisition ---
        def _control_head_changed(self, *_):
            if not self.info or self.ctl_head.count() == 0:
                return
            h = self.info["heads"][self.ctl_head.currentData()]
            lo, hi = h["limits"]
            for s in (self.range_start, self.range_stop):
                s.blockSignals(True)
                s.setRange(lo, hi)
                s.blockSignals(False)
            self.range_slider.blockSignals(True)
            self.range_slider.setRange(int(math.ceil(lo * 10)) + 1, int(hi * 10))
            self.range_slider.blockSignals(False)
            self.range_start.setValue(lo)
            if h.get("gain") is not None:
                self.gain.setValue(float(h["gain"]))
            if h.get("tvg") is not None:
                self.tvg.setValue(float(h["tvg"]))
            self.tvg.setEnabled(bool(self.info["live"]) and h.get("tvg") is not None)   # not every sonar has TVG
            self.range_stop.setValue(min(hi, float(self.settings.value(f"range_stop/{h['model']}", hi))))
            self.range_slider.setValue(int(self.range_stop.value() * 10))

        def _show_sonar_range(self, start, stop):
            """Shows the range the sonar is really using (from its pings), unless the user is editing."""
            if self.range_slider.isSliderDown() or self.range_start.hasFocus() or self.range_stop.hasFocus():
                return
            for box, value in ((self.range_start, start), (self.range_stop, stop)):
                if abs(box.value() - value) > 0.005:
                    box.blockSignals(True)
                    box.setValue(value)
                    box.blockSignals(False)
            self.range_slider.blockSignals(True)
            self.range_slider.setValue(int(round(stop * 10)))
            self.range_slider.blockSignals(False)

        def _slider_released(self):
            self.range_stop.setValue(self.range_slider.value() / 10.0)
            self.apply_range()

        def apply_range(self):
            if not (self.info and self.info["live"]):
                return
            start, stop = self.range_start.value(), self.range_stop.value()
            if stop <= start:
                stop = min(start + 0.5, self.range_stop.maximum())
                self.range_stop.setValue(stop)
            self.range_slider.blockSignals(True)
            self.range_slider.setValue(int(stop * 10))
            self.range_slider.blockSignals(False)
            head = self.ctl_head.currentData()
            if self._call(self.c.range, head, start, stop) is not None:
                self.settings.setValue(f"range_stop/{self.info['heads'][head]['model']}", stop)

        def apply_setting(self, name):
            if not (self.info and self.info["live"]):
                return
            spin = self.gain if name == "gain" else self.tvg
            self._call(getattr(self.c, name), self.ctl_head.currentData(), spin.value())

        def _ensure_sub(self):
            if self.sub is None:
                self.sub = Subscriber(self.service.pub, serial=self.info["serial"])
                time.sleep(0.2)                                          # the subscription reaches PUB

        def start(self):
            heads = self.selected_heads()
            if not heads:
                QtWidgets.QMessageBox.information(self, "opensonar", "Tick at least one head.")
                return
            self._ensure_sub()
            speed = None if self.info["live"] else self.speed.currentData()
            if self._call(self.c.start, heads, True, self.smooth.isChecked(), speed) is None:
                self._close_sub()
                return
            self._rate = []
            self.btn_play.setChecked(True)
            self.btn_play.setText("⏸")
            self.status["running"] = True

        def stop(self):
            self._call(self.c.stop)
            self.btn_play.setChecked(False)
            self.btn_play.setText("▶")
            self.status["running"] = False

        def _close_sub(self):
            if self.sub is not None:
                self.sub.close()
                self.sub = None

        # --- playback (files) ---
        def _play_clicked(self):
            if self.btn_play.isChecked():
                self.start()
            else:
                self.stop()

        def _speed_changed(self, *_):
            if self.info and not self.info["live"] and self.status.get("running"):
                self.start()

        def step(self, delta):
            if not self.info or self.info["live"]:
                return
            self._ensure_sub()
            self.btn_play.setChecked(False)
            self.btn_play.setText("▶")
            self._call(self.c.step, delta, True, self.smooth.isChecked(), quiet=True)
            self.refresh_status()

        def goto(self, index):
            if not self.info or self.info["live"]:
                return
            self._ensure_sub()
            if self._call(self.c.seek, index) is None:
                return
            if not self.status.get("running"):
                self._call(self.c.step, 1, True, self.smooth.isChecked(), quiet=True)   # show that ping
            self.refresh_status()

        # --- receiving ---
        def poll(self):
            """Drains the subscription and draws the latest ping of each head."""
            if self.sub is None:
                return
            latest = {}
            for _ in range(200):
                m = self.sub.recv(timeout=0)
                if m is None:
                    break
                self.received += 1
                seq = m.meta["seq"]
                if self._last_seq is not None and seq > self._last_seq + 1:
                    self.lost += seq - self._last_seq - 1
                self._last_seq = seq
                self._rate.append(time.time())
                latest[m.meta["head"]] = m
                self._last_msg = m
            for h, m in latest.items():
                v = self.views.get(h)
                if v is not None and m.image is not None:
                    v.show_ping(m, self.mappers[h])
            m = latest.get(self.ctl_head.currentData())
            if m is not None and self.info and self.info["live"]:
                self._show_sonar_range(m.ping.range_start, m.ping.range_stop)
            now = time.time()
            self._rate = [t for t in self._rate if now - t < 2.0]
            if latest or (self._rate and now - self._rate[-1] > 2):
                self.status_rate.setText(f"{len(self._rate) / 2.0:.1f} pings/s")
                self.status_lost.setText(f"lost {self.lost}")
                self.status_lost.setStyleSheet("color: #e33" if self.lost else "")
            if self._last_msg is not None:
                self.status_utc.setText("UTC " + self._last_msg.meta["utc"][11:23])

        def refresh_status(self):
            """Polls the service: recording indicator, playback position, sonar clock offset."""
            if self.info is None:
                self.status_rec.setText("")
                return
            st = self._call(self.c.status, quiet=True)
            if st is None:
                return
            self.status = st
            rec = st.get("record")
            video = getattr(self, "video", None)
            parts = []
            if rec:
                parts.append(f"{hms(rec['elapsed'])} · {rec['pings']} pings" +
                             (f" · {human_bytes(rec['bytes'])}" if rec["bytes"] else ""))
            if video is not None:
                if not rec:
                    parts.append(hms(time.time() - self.video_started))
                parts.append(f"video {video.frames} frames")
            self.status_rec.setText("● REC " + " · ".join(parts) if parts else "")
            off = st.get("clock_offset")
            if off is None or not self.info["live"]:
                self.status_clock.setText("")
            elif abs(off) > 86400:                                       # never set (e.g. near 1970)
                self.status_clock.setText(f"sonar clock {off / 86400:+.0f} days (not set; times corrected)")
            else:
                self.status_clock.setText(f"sonar clock {off:+.3f} s")
            pb = st.get("playback")
            if pb and pb["n_pings"]:
                n = pb["n_pings"]
                pos = max(0, min(pb["position"] - 1, n - 1))             # the ping on screen
                if not self.timeline.isSliderDown():
                    self.timeline.blockSignals(True)
                    self.timeline.setRange(0, n - 1)
                    self.timeline.setValue(pos)
                    self.timeline.blockSignals(False)
                self.goto_box.setRange(0, n - 1)
                self.time_label.setText(f"{pos + 1} / {n}")
                if not st["running"] and self.btn_play.isChecked():      # end of the file
                    self.btn_play.setChecked(False)
                    self.btn_play.setText("▶")

        # --- tools ---
        def _cursor(self, view, x, y):
            r, b = polar(x, y)
            self.status_cursor.setText(f"head {view.head}:  x {x:+.2f} m  y {y:.2f} m  ·  "
                                       f"range {r:.2f} m  bearing {b:+.1f}°")

        def _ruler_toggled(self, on):
            for v in self.views.values():
                v.ruler_on = bool(on)
                if not on:
                    v.clear_ruler()

        def save_screen(self, path):
            self.panels.grab().save(path, "PNG")
            return path

        def save_tiff(self, path):
            """Saves the u16 image on screen of every shown head (16-bit TIFF; _h<N> per head when several)."""
            from PIL import Image
            shown = [(h, v) for h, v in self.views.items() if v.last is not None]
            base, ext = os.path.splitext(path)
            out = []
            for h, v in shown:
                target = path if len(shown) == 1 else f"{base}_h{h}{ext or '.tif'}"
                Image.fromarray(v.last[0].astype(np.uint16)).save(target)
                out.append(target)
            return out

        def _ask_path(self, title, name, filt, key):
            folder = self.settings.value(key, os.path.expanduser("~"))
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, title, os.path.join(folder, name), filt)
            if path:
                self.settings.setValue(key, os.path.dirname(path))
            return path

        def _save_screen_clicked(self):
            path = self._ask_path("Save screen", utc_now_name() + ".png", "PNG (*.png)", "save_dir")
            if path:
                self.save_screen(path)

        def _save_tiff_clicked(self):
            if not any(v.last is not None for v in self.views.values()):
                return
            path = self._ask_path("Save image (16-bit TIFF)", utc_now_name() + ".tif", "TIFF (*.tif *.tiff)",
                                  "save_dir")
            if path:
                self.save_tiff(path)

        # --- recording and marks ---
        def _record_clicked(self, on):
            if on:
                dlg = RecordDialog(self, self.settings.value("record_dir", os.path.expanduser("~")), utc_now_name(),
                                   str(self.settings.value("record_formats", "son")).split(","))
                if dlg.exec() == QtWidgets.QDialog.Accepted:
                    base, formats = dlg.values()
                    self.settings.setValue("record_dir", os.path.dirname(base))
                    self.settings.setValue("record_formats", ",".join(formats))
                    if formats and self.start_recording(base, formats):
                        return
                self.act_record.setChecked(False)
            else:
                self.stop_recording()

        def start_recording(self, path, formats=("son",)):
            """Records in any combination of formats: .son and per-ping images in the service, MP4 here."""
            from .media import VideoWriter
            base = os.path.abspath(path[:-4] if path.lower().endswith(".son") else path)
            service_formats = [f for f in formats if f != "mp4"]
            if service_formats and self._call(self.c.record, base, service_formats) is None:
                self.act_record.setChecked(False)
                return False
            self.video = None
            if "mp4" in formats:
                self.video = VideoWriter(base + ".mp4", fps=10)
                self.video_started = time.time()
                self.video_timer = QtCore.QTimer(self)
                self.video_timer.timeout.connect(self._video_frame)
                self.video_timer.start(100)
            self.recording = (base, list(formats))
            self.act_record.setChecked(True)
            self.refresh_status()
            return True

        def _video_frame(self):
            if self.video is not None:
                self.video.add(qimage_rgb(self.panels.grab().toImage()))

        def stop_recording(self):
            r = {}
            if getattr(self, "recording", None) and any(f != "mp4" for f in self.recording[1]):
                timeout, self.c.timeout = self.c.timeout, 600.0      # waits for the images still being written
                try:
                    r = self._call(self.c.stop_record) or {}
                finally:
                    self.c.timeout = timeout
            video = getattr(self, "video", None)
            if video is not None:
                self.video_timer.stop()
                video.close()
                r["video"], r["video_frames"] = video.path, video.frames
                self.video = None
            self.recording = None
            self.act_record.setChecked(False)
            self.refresh_status()
            parts = []
            if r.get("path"):
                parts.append(f"{r['pings']} pings" + (f" ({human_bytes(r['bytes'])})" if r.get("bytes") else "")
                             + f" — {', '.join(r.get('formats', []))} at {r['path']}")
            if r.get("video"):
                parts.append(f"video {r['video_frames']} frames at {r['video']}")
            if r.get("marks"):
                parts.append(f"{r['marks']} mark(s) in {os.path.basename(r['events'])}")
            if parts:
                self.statusBar().showMessage("Recorded: " + "; ".join(parts), 15000)
            return r

        def add_mark(self, text=""):
            r = self._call(self.c.mark, text)
            if r:
                where = "saved with the recording" if r.get("file") else "published (not recording)"
                self.statusBar().showMessage(f"Mark {r['mark']['utc']}  {text}  — {where}", 6000)
            return r

        def _mark_clicked(self):
            text, ok = QtWidgets.QInputDialog.getText(self, "Event mark", "Text (optional):")
            if ok:
                self.add_mark(text)

        # --- export ---
        def export_pings(self, head, first, last, formats, kind, out_dir, progress=None):
            """Exports pings first..last of a head of the open file in several formats (media.export);
            the video uses the current display settings."""
            from .media import export
            if isinstance(formats, str):
                formats = [formats]
            mapper = display.ColorMapper(self.maps[self.cmap.currentText()](), self.gamma.value(),
                                         self.auto.isChecked(), self.sensitivity.value(), self.intensity.value())
            return export(self.info["source"], out_dir, formats, kind, range(first, last + 1), head, mapper,
                          progress=progress)

        def _export_clicked(self):
            if not self.info or self.info["live"]:
                return
            heads = [(h["index"], f"{h['index']}  {h['model']}") for h in self.info["heads"] if h["n_pings"]]
            n = {h["index"]: h["n_pings"] for h in self.info["heads"]}
            dlg = ExportDialog(self, self.info["source"], heads, n,
                               self.settings.value("export_dir", os.path.dirname(self.info["source"])),
                               str(self.settings.value("export_formats", "png")).split(","))
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            v = dlg.values()
            if not v["formats"]:
                return
            self.settings.setValue("export_dir", v["out_dir"])
            self.settings.setValue("export_formats", ",".join(v["formats"]))
            total = v["last"] - v["first"] + 1
            bar = QtWidgets.QProgressDialog("Exporting…", "Cancel", 0, total, self)
            bar.setWindowModality(QtCore.Qt.WindowModal)

            def progress(done, _total):
                bar.setValue(done)
                QtWidgets.QApplication.processEvents()
                return not bar.wasCanceled()
            try:
                done = self.export_pings(v["head"], v["first"], v["last"], v["formats"], v["kind"], v["out_dir"],
                                         progress)
            except Exception as e:  # noqa: BLE001 — shown to the user
                QtWidgets.QMessageBox.warning(self, "opensonar", f"Export failed: {e}")
                return
            finally:
                bar.close()
            self.statusBar().showMessage(f"Exported {done} ping(s) as {', '.join(v['formats'])} to {v['out_dir']}",
                                         8000)

        # --- display ---
        def _new_mapper(self):
            return display.ColorMapper(self.maps[self.cmap.currentText()](), self.gamma.value(),
                                       self.auto.isChecked(), self.sensitivity.value(), self.intensity.value())

        def _display_changed(self, *_):
            auto = self.auto.isChecked()
            for w in self._manual_widgets:
                w.setEnabled(not auto)
            colors = self.maps[self.cmap.currentText()]()
            if self._restored:
                self.settings.setValue("colormap", self.cmap.currentText())
            for m in self.mappers.values():
                m.set_colors(colors)
                m.set_auto(auto)
                if auto:
                    m.gamma, m._lut = display.DEFAULTS["gamma"], None
                else:
                    m.set_manual(self.sensitivity.value(), self.intensity.value(), self.gamma.value())
            for h, v in self.views.items():
                v.redraw(self.mappers[h])

        def _smooth_changed(self, on):
            if self.info is not None:
                self._call(self.c.image, True, bool(on))

        def _grid_changed(self, on):
            for v in self.views.values():
                v.grid_on = bool(on)
                v._grid_key = None
                if v.last is not None:
                    v.show_ping(v.last[2], self.mappers[v.head])

        def choose_cmap_folder(self):
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder with .cmap files")
            if not d:
                return
            self.settings.setValue("cmap_folder", d)
            self.cmap_folders = [d]
            self.maps = display.colormaps(self.cmap_folders)
            current = self.cmap.currentText()
            self.cmap.blockSignals(True)
            self.cmap.clear()
            self.cmap.addItems(list(self.maps))
            self.cmap.setCurrentText(current)
            self.cmap.blockSignals(False)
            self._display_changed()

        # --- closing ---
        def closeEvent(self, ev):
            self.save_settings()
            if getattr(self, "recording", None):
                self.stop_recording()
            self.timer.stop()
            self.status_timer.stop()
            self._close_sub()
            if self.service.owned:
                self.service.stop()               # also closes any recording in the service
            else:
                self.service.client.close()       # someone else's service keeps running (and recording)
            super().closeEvent(ev)

    return MainWindow(service or Service(), settings)


def main(argv=None):
    QtCore, _, QtWidgets = _qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv if argv is None else argv)
    app.setOrganizationName(ORG)
    app.setApplicationName(APP)
    try:
        service = Service()
    except ServerError as e:
        QtWidgets.QMessageBox.critical(None, "opensonar", str(e))
        return 1
    w = build(service)
    w.show()
    return app.exec()
