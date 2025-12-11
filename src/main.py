#!/usr/bin/env python3
# main.py - PyQt6 GUI with Connect → Record/Stop and real connection verification

import os
import re
import shlex
import subprocess
import time
import signal
from functools import partial
from PyQt6 import QtCore, QtGui, QtWidgets
from core import BluezTarget, pair_device, connect_device, is_vulnerable

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BLUE_SPY_PATH = os.path.join(BASE_DIR, "BlueSpy.py")
PYTHON = "python3"
BT_DEVICE_RE = re.compile(r"Device\s+([0-9A-F:]{17})\s+(.+)", re.IGNORECASE)


class BluetoothScannerThread(QtCore.QThread):
    device_found = QtCore.pyqtSignal(str, str)
    log = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
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
            )
        except Exception as e:
            self.log.emit(f"[scanner] failed to start bluetoothctl: {e}")
            return

        self.log.emit("[scanner] bluetoothctl started, enabling scan...")
        try:
            if self.proc.stdin:
                self.proc.stdin.write("power on\n")
                self.proc.stdin.flush()
                time.sleep(0.2)
                self.proc.stdin.write("scan on\n")
                self.proc.stdin.flush()

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
            self.log.emit(f"[scanner] error: {e}")

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


class ConnectThread(QtCore.QThread):
    finished_signal = QtCore.pyqtSignal(str, bool, bool, str)
    log = QtCore.pyqtSignal(str)

    def __init__(self, mac):
        super().__init__()
        self.mac = mac

    def run(self):
        target = BluezTarget(self.mac)
        self.log.emit(f"[connect] Trying to pair and connect {self.mac}...")
        try:
            paired = pair_device(target)
            connected = connect_device(target)
            vulnerable = is_vulnerable(target)
            self.finished_signal.emit(self.mac, connected, vulnerable, "Connected successfully")
            self.log.emit(f"[connect] {self.mac} connected. Vulnerable: {'Yes' if vulnerable else 'No'}")
        except Exception as e:
            self.finished_signal.emit(self.mac, False, False, f"Error: {e}")
            self.log.emit(f"[connect] {self.mac} connection failed: {e}")


class RecorderThread(QtCore.QThread):
    finished_signal = QtCore.pyqtSignal(str, bool, str)
    log = QtCore.pyqtSignal(str)

    def __init__(self, mac, outfile):
        super().__init__()
        self.mac = mac
        self.outfile = outfile
        self.proc = None
        self._stop = False

    def stop(self):
        self._stop = True
        if self.proc:
            try:
                import os, signal
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                pass

    def run(self):
        cmd = f"{PYTHON} {shlex.quote(BLUE_SPY_PATH)} -a {self.mac} -f {shlex.quote(self.outfile)}"
        self.log.emit(f"[recorder] starting: {cmd}")
        try:
            self.proc = subprocess.Popen(
                shlex.split(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid,
            )
        except Exception as e:
            self.finished_signal.emit(self.mac, False, f"Failed to start: {e}")
            return

        try:
            for line in self.proc.stdout:
                if self._stop:
                    self.finished_signal.emit(self.mac, False, "Stopped by user")
                    return
                if line:
                    self.log.emit(f"[recorder:{self.mac}] {line.rstrip()}")
            code = self.proc.wait()
            if self._stop:
                self.finished_signal.emit(self.mac, False, "Stopped by user")
            elif code == 0:
                self.finished_signal.emit(self.mac, True, "Recording completed")
            else:
                self.finished_signal.emit(self.mac, False, f"Recorder exited with code {code}")
        except Exception as e:
            self.finished_signal.emit(self.mac, False, f"Recorder error: {e}")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GreenHack Bluetooth Scanner")
        self.resize(1100, 640)

        self.devices = {}
        self.active_recorders = {}
        self.connect_threads = {}

        self._setup_ui()
        self.scanner = BluetoothScannerThread()
        self.scanner.device_found.connect(self.on_device_found)
        self.scanner.log.connect(self.append_log)

    def _setup_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        toolbar = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("GreenHack Bluetooth Scanner")
        title.setFont(QtGui.QFont("Courier", 16, QtGui.QFont.Weight.Bold))
        title.setStyleSheet("color:#00FF66;")
        toolbar.addWidget(title)
        toolbar.addStretch()
        self.start_btn = QtWidgets.QPushButton("Start scanning")
        self.start_btn.clicked.connect(self.toggle_scanning)
        toolbar.addWidget(self.start_btn)
        layout.addLayout(toolbar)

        main_area = QtWidgets.QHBoxLayout()
        layout.addLayout(main_area)

        # Table
        table_card = QtWidgets.QFrame()
        table_card.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        table_card.setMinimumWidth(700)
        table_card.setStyleSheet(self._card_style())
        table_layout = QtWidgets.QVBoxLayout(table_card)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["MAC", "Name", "Last Seen", "Status", "Vulnerable", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        table_layout.addWidget(self.table)
        main_area.addWidget(table_card, stretch=2)

        # Logs
        log_card = QtWidgets.QFrame()
        log_card.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        log_card.setMinimumWidth(350)
        log_card.setStyleSheet(self._card_style())
        log_layout = QtWidgets.QVBoxLayout(log_card)
        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(self._log_style())
        self.log_view.setFont(QtGui.QFont("Courier", 10))
        log_layout.addWidget(self.log_view)
        main_area.addWidget(log_card, stretch=1)

    def _card_style(self):
        return "QFrame { background-color:#0b0b0b; border:1px solid #0f3; border-radius:8px;}"

    def _log_style(self):
        return "QTextEdit { background-color:#000; color:#00FF66; border:none;}"

    def toggle_scanning(self):
        if not self.scanner.isRunning():
            self.scanner.start()
            self.start_btn.setText("Stop scanning")
            self.append_log("[ui] Scanner started...")
        else:
            self.scanner.stop()
            self.scanner.wait(1000)
            self.start_btn.setText("Start scanning")
            self.append_log("[ui] Scanner stopped...")

    def on_device_found(self, mac, name):
        now = time.strftime("%H:%M:%S")
        if mac in self.devices:
            row = self.devices[mac]["row"]
            self.table.item(row, 1).setText(name)
            self.table.item(row, 2).setText(now)
            self.devices[mac]["name"] = name
            self.devices[mac]["last_seen"] = now
        else:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(mac))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(name))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(now))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem("Idle"))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem("Unknown"))

            btn = QtWidgets.QPushButton("Connect")
            btn.setStyleSheet("background-color:#00FF66;color:black;")
            btn.clicked.connect(partial(self.on_connect_clicked, mac, row))
            self.table.setCellWidget(row, 5, btn)

            self.devices[mac] = {"name": name, "last_seen": now, "row": row, "connected": False, "vulnerable": None}

    def on_connect_clicked(self, mac, row):
        btn = self.table.cellWidget(row, 5)
        btn.setEnabled(False)
        self.table.item(row, 3).setText("Connecting...")
        thread = ConnectThread(mac)
        thread.log.connect(self.append_log)
        thread.finished_signal.connect(self.on_connect_finished)
        self.connect_threads[mac] = thread
        thread.start()

    def check_connected(self, mac):
        try:
            output = subprocess.run(["bluetoothctl", "info", mac], capture_output=True, text=True).stdout
            return "Connected: yes" in output and "Paired: yes" in output
        except Exception:
            return False

    def on_connect_finished(self, mac, success, vulnerable, msg):
        row = self.devices[mac]["row"]
        connected = self.check_connected(mac)
        self.devices[mac]["connected"] = connected
        self.devices[mac]["vulnerable"] = vulnerable if connected else None
        self.table.item(row, 4).setText("Yes" if vulnerable and connected else "No" if connected else "Unknown")
        self.table.item(row, 3).setText("Idle" if connected else "Error")
        btn = self.table.cellWidget(row, 5)
        if connected:
            btn.setText("Record")
            btn.setEnabled(True)
            btn.setStyleSheet("background-color:#00FF66;color:black;")
            btn.clicked.disconnect()
            btn.clicked.connect(partial(self.on_record_clicked, mac, row))
            self.append_log(f"[ui] Device {mac} connected successfully. Vulnerable: {'Yes' if vulnerable else 'No'}")
        else:
            btn.setText("Connect")
            btn.setEnabled(True)
            btn.setStyleSheet("background-color:#00FF66;color:black;")
            self.append_log(f"[!] Device {mac} did not accept pairing. Recording will use system mic.")

    def on_record_clicked(self, mac, row):
        btn = self.table.cellWidget(row, 5)
        info = self.devices[mac]
        if mac in self.active_recorders:
            rec = self.active_recorders[mac]
            btn.setEnabled(False)
            btn.setText("Stopping...")
            rec.stop()
            return

        safe_name = re.sub(r"[^\w\-_. ]", "_", info["name"])
        filename = f"{safe_name}.wav"
        rec_thread = RecorderThread(mac, filename)
        rec_thread.log.connect(self.append_log)
        rec_thread.finished_signal.connect(self.on_record_finished)
        self.active_recorders[mac] = rec_thread
        rec_thread.start()
        btn.setText("Stop")
        btn.setStyleSheet("background-color:#FF3333;color:black;")
        self.table.item(row, 3).setText("Recording...")
        self.append_log(f"[ui] Started recording {mac} → {filename}")

    def on_record_finished(self, mac, success, msg):
        row = self.devices[mac]["row"]
        btn = self.table.cellWidget(row, 5)
        btn.setText("Record")
        btn.setStyleSheet("background-color:#00FF66;color:black;")
        btn.setEnabled(True)
        self.table.item(row, 3).setText("Idle" if success else "Error")
        self.active_recorders.pop(mac, None)
        self.append_log(f"[ui] Recording finished for {mac}: {msg}")

    def append_log(self, text):
        ts = time.strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}] {text}")
        self.log_view.moveCursor(QtGui.QTextCursor.MoveOperation.End)


def main():
    import sys
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
