#!/usr/bin/env python3
# main.py -- PyQt6 Bluetooth scanner + recorder GUI (green/black "hacking" theme)
# Requirements: PyQt6, Python 3.8+
# Adjust BLUE_SPY_CMD if BlueSpy.py is located elsewhere or needs a different interpreter.

import sys
import re
import shlex
import subprocess
import time
from functools import partial

from PyQt6 import QtCore, QtGui, QtWidgets

# Edit this to point to your BlueSpy script/executable if needed
BLUE_SPY_CMD = "python3 BlueSpy.py"

# Regex to parse lines like: "Device XX:XX:XX:XX:XX:XX DeviceName"
BT_DEVICE_RE = re.compile(r"Device\s+([0-9A-F:]{17})\s+(.+)", re.IGNORECASE)


class BluetoothScannerThread(QtCore.QThread):
    device_found = QtCore.pyqtSignal(str, str)  # mac, name
    log = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.proc = None

    def run(self):
        self._running = True
        try:
            self.proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
        except Exception as e:
            self.log.emit(f"[scanner] failed to start bluetoothctl: {e}")
            self._running = False
            return

        self.log.emit("[scanner] bluetoothctl started, enabling scan...")
        try:
            if self.proc.stdin:
                try:
                    self.proc.stdin.write("scan on\n")
                    self.proc.stdin.flush()
                except Exception as e:
                    self.log.emit(f"[scanner] failed to write to bluetoothctl stdin: {e}")
            else:
                self.log.emit("[scanner] no stdin available for bluetoothctl")

            # Read lines continuously
            while self._running:
                line = self.proc.stdout.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                line = line.strip()
                if line:
                    self.log.emit(f"[bluetoothctl] {line}")
                    m = BT_DEVICE_RE.search(line)
                    if m:
                        mac = m.group(1).upper()
                        name = m.group(2).strip()
                        self.device_found.emit(mac, name)
        except Exception as e:
            self.log.emit(f"[scanner] error while reading bluetoothctl: {e}")

        # cleanup
        try:
            if self.proc and self.proc.stdin:
                self.proc.stdin.write("scan off\n")
                self.proc.stdin.flush()
        except Exception:
            pass
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass
        self.log.emit("[scanner] stopped")

    def stop(self):
        self._running = False
        try:
            if self.proc and self.proc.stdin:
                self.proc.stdin.write("scan off\n")
                self.proc.stdin.flush()
        except Exception:
            pass
        try:
            if self.proc:
                self.proc.kill()
        except Exception:
            pass


class RecorderThread(QtCore.QThread):
    log = QtCore.pyqtSignal(str)
    finished_signal = QtCore.pyqtSignal(bool, str)  # success, message

    def __init__(self, mac: str, name: str, parent=None):
        super().__init__(parent)
        self.mac = mac
        self.name = name

    def run(self):
        # sanitize name for filename
        safe_name = re.sub(r"[^\w\-_. ]", "_", self.name).strip() or self.mac.replace(":", "")
        filename = f"{safe_name}.wav"
        cmd = f"{BLUE_SPY_CMD} -a --macaddress {self.mac} -f {shlex.quote(filename)}"
        self.log.emit(f"[recorder] starting: {cmd}")
        try:
            proc = subprocess.Popen(
                shlex.split(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            msg = f"[recorder] failed to start recorder process: {e}"
            self.log.emit(msg)
            self.finished_signal.emit(False, msg)
            return

        try:
            for line in proc.stdout:
                if line is None:
                    break
                line = line.rstrip()
                if line:
                    self.log.emit(f"[recorder:{self.mac}] {line}")
            proc.wait()
            code = proc.returncode
            if code == 0:
                msg = f"[recorder] finished successfully, saved to: {filename}"
                self.log.emit(msg)
                self.finished_signal.emit(True, msg)
            else:
                msg = f"[recorder] exited with code {code}"
                self.log.emit(msg)
                self.finished_signal.emit(False, msg)
        except Exception as e:
            msg = f"[recorder] error while streaming output: {e}"
            self.log.emit(msg)
            self.finished_signal.emit(False, msg)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GreenHack Bluetooth Scanner")
        self.resize(1000, 640)
        self.devices = {}  # mac -> {name, last_seen, row}

        self._setup_ui()
        self.scanner = BluetoothScannerThread()
        self.scanner.device_found.connect(self.on_device_found)
        self.scanner.log.connect(self.append_log)

    def _setup_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # toolbar row
        toolbar = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("GreenHack Bluetooth Scanner")
        title.setFont(QtGui.QFont("Courier", 16, QtGui.QFont.Weight.Bold))
        title.setStyleSheet("color: #00FF66;")
        toolbar.addWidget(title)
        toolbar.addStretch()

        self.start_btn = QtWidgets.QPushButton("Start scanning")
        self.start_btn.setFixedHeight(36)
        self.start_btn.clicked.connect(self.toggle_scanning)
        toolbar.addWidget(self.start_btn)

        layout.addLayout(toolbar)

        # main area: table + logs
        main_area = QtWidgets.QHBoxLayout()
        layout.addLayout(main_area, stretch=1)

        # Table card (left)
        table_card = QtWidgets.QFrame()
        table_card.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        table_card.setMinimumWidth(620)
        table_card.setStyleSheet(self._card_style())
        table_layout = QtWidgets.QVBoxLayout(table_card)
        table_layout.setContentsMargins(8, 8, 8, 8)

        tlabel = QtWidgets.QLabel("Live Devices")
        tlabel.setStyleSheet("color:#00FF66; font-weight:bold;")
        table_layout.addWidget(tlabel)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["MAC Address", "Name", "Last Seen", "Status", "Action"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setStyleSheet(self._table_style())
        table_layout.addWidget(self.table)

        main_area.addWidget(table_card, stretch=2)

        # Log card (right)
        log_card = QtWidgets.QFrame()
        log_card.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        log_card.setMinimumWidth(320)
        log_card.setStyleSheet(self._card_style())
        log_layout = QtWidgets.QVBoxLayout(log_card)
        log_layout.setContentsMargins(8, 8, 8, 8)
        llabel = QtWidgets.QLabel("Logs")
        llabel.setStyleSheet("color:#00FF66; font-weight:bold;")
        log_layout.addWidget(llabel)

        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(self._log_style())
        self.log_view.setFont(QtGui.QFont("Courier", 10))
        log_layout.addWidget(self.log_view)

        main_area.addWidget(log_card, stretch=1)

        # bottom status
        self.status = QtWidgets.QLabel("Idle")
        self.status.setStyleSheet("color:#00FF66;")
        layout.addWidget(self.status)

        self.setStyleSheet("background-color: #020202;")

    def _card_style(self):
        return """
            QFrame {
                background-color: #0b0b0b;
                border: 1px solid #0f3;
                border-radius: 8px;
            }
        """

    def _table_style(self):
        return """
            QTableWidget {
                background-color: #020202;
                color: #00FF66;
                gridline-color: #003300;
                font-family: Courier;
            }
            QHeaderView::section {
                background-color: #001100;
                color: #00FF66;
                padding: 4px;
                border: 1px solid #003300;
            }
            QTableWidget::item {
                padding: 6px;
            }
        """

    def _log_style(self):
        return """
            QTextEdit {
                background-color: #000;
                color: #00FF66;
                border: none;
            }
        """

    @QtCore.pyqtSlot()
    def toggle_scanning(self):
        if not self.scanner.isRunning():
            self.start_scanning()
        else:
            self.stop_scanning()

    def start_scanning(self):
        self.append_log("[ui] Starting scanner...")
        self.scanner.start()
        self.start_btn.setText("Stop scanning")
        self.status.setText("Scanning...")

    def stop_scanning(self):
        self.append_log("[ui] Stopping scanner...")
        self.scanner.stop()
        # wait briefly for thread to exit
        self.scanner.wait(1000)
        self.start_btn.setText("Start scanning")
        self.status.setText("Idle")

    @QtCore.pyqtSlot(str, str)
    def on_device_found(self, mac, name):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if mac in self.devices:
            row = self.devices[mac]["row"]
            # update name and last seen
            self.table.item(row, 1).setText(name)
            self.table.item(row, 2).setText(now)
            self.devices[mac]["name"] = name
            self.devices[mac]["last_seen"] = now
            self.append_log(f"[ui] updated device {mac} ({name})")
        else:
            row = self.table.rowCount()
            self.table.insertRow(row)

            mac_item = QtWidgets.QTableWidgetItem(mac)
            mac_item.setFlags(mac_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            name_item = QtWidgets.QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            seen_item = QtWidgets.QTableWidgetItem(now)
            seen_item.setFlags(seen_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            status_item = QtWidgets.QTableWidgetItem("Idle")
            status_item.setFlags(status_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)

            self.table.setItem(row, 0, mac_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, seen_item)
            self.table.setItem(row, 3, status_item)

            btn = QtWidgets.QPushButton("Record")
            btn.setStyleSheet("color:#000; background-color:#00FF66; padding:4px;")
            btn.clicked.connect(partial(self.on_record_clicked, mac, name, row))
            self.table.setCellWidget(row, 4, btn)

            self.devices[mac] = {"name": name, "last_seen": now, "row": row}
            self.append_log(f"[ui] new device {mac} ({name})")

    def on_record_clicked(self, mac, name, row):
        widget = self.table.cellWidget(row, 4)
        if isinstance(widget, QtWidgets.QPushButton):
            widget.setEnabled(False)
            widget.setText("Starting...")

        status_item = self.table.item(row, 3)
        if status_item:
            status_item.setText("Recording...")

        recorder = RecorderThread(mac, name, parent=self)
        recorder.log.connect(self.append_log)
        # Attach mac,row,widget as fixed args to the handler
        recorder.finished_signal.connect(partial(self.on_record_finished, mac, row, widget))
        recorder.start()

    @QtCore.pyqtSlot(bool, str)
    def on_record_finished(self, mac, row, widget, success: bool, message: str):
        status_item = self.table.item(row, 3)
        if status_item:
            status_item.setText("Idle" if success else "Error")

        if isinstance(widget, QtWidgets.QPushButton):
            widget.setEnabled(True)
            widget.setText("Record")

        self.append_log(f"[ui] recording for {mac} finished: {message}")

    @QtCore.pyqtSlot(str)
    def append_log(self, text):
        ts = time.strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}] {text}")
        # auto-scroll
        self.log_view.moveCursor(QtGui.QTextCursor.MoveOperation.End)

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
