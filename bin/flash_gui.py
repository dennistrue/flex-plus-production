#!/usr/bin/env python3
"""Browser-based GUI for flashing Flex Plus production bundles."""

from __future__ import annotations

import csv
import http.server
import json
import os
import platform
import glob
import re
import shlex
import shutil
import subprocess
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import ClassVar

PRODUCTION_DIR = Path(__file__).resolve().parent
PASSWORD_DB_PATH = PRODUCTION_DIR / "passwords.csv"
DEFAULT_PASSWORD = "12345678"
FLOW_VERSION = "gui-1.0.0"
MANIFEST_PATH = PRODUCTION_DIR / "release" / "manifest.json"
SERIAL_MIN = 1
SERIAL_MAX = 100
YEAR_MIN = 0
YEAR_MAX = 99
MONTH_MIN = 1
MONTH_MAX = 12
IDENTIFIER_PREFIX = "FP"
ANSI_ESCAPE = re.compile(r"\x1B\[[0-9;?]*[ -/]*[@-~]")


def validate_year(value: int) -> None:
    if not (YEAR_MIN <= value <= YEAR_MAX):
        raise ValueError(f"Year must be between {YEAR_MIN:02d} and {YEAR_MAX:02d}.")


def validate_month(value: int) -> None:
    if not (MONTH_MIN <= value <= MONTH_MAX):
        raise ValueError(f"Month must be between {MONTH_MIN:02d} and {MONTH_MAX:02d}.")


def format_identifier(batch: int, year: int, month: int, serial: int) -> str:
    return f"{IDENTIFIER_PREFIX}{batch:02d}-{year:02d}{month:02d}{serial:04d}"


class PasswordDatabase:
    def __init__(self, path: Path, default_password: str) -> None:
        self.path = path
        self.default_password = default_password
        self.entries: dict[tuple[int, int], str] = {}
        self.loaded_from_disk = False

    def load(self) -> None:
        if not self.path.exists():
            self.entries = {}
            self.loaded_from_disk = False
            return
        self.loaded_from_disk = True
        with self.path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            required = {"batch", "serial", "password"}
            if not required.issubset(reader.fieldnames or set()):
                raise SystemExit("Password CSV must contain batch,serial,password columns.")
            self.entries = {}
            for row in reader:
                try:
                    batch = int(row["batch"])
                    serial = int(row["serial"])
                except (TypeError, ValueError) as exc:
                    raise SystemExit(f"Invalid batch/serial value in {self.path}: {row}") from exc
                password = row["password"].strip()
                if not (SERIAL_MIN <= serial <= SERIAL_MAX):
                    raise SystemExit(f"Serial {serial} out of supported range {SERIAL_MIN}-{SERIAL_MAX}.")
                if len(password) < 8 or len(password) > 63:
                    raise SystemExit(f"Password for batch {batch} serial {serial} violates length constraints.")
                key = (batch, serial)
                if key in self.entries:
                    raise SystemExit(f"Duplicate password entry for batch {batch} serial {serial:04d}.")
                self.entries[key] = password

    def lookup(self, batch: int, serial: int, year: int, month: int) -> dict[str, object]:
        if batch <= 0:
            raise ValueError("Batch number must be positive.")
        if not (SERIAL_MIN <= serial <= SERIAL_MAX):
            raise ValueError(f"Serial must be between {SERIAL_MIN} and {SERIAL_MAX}.")
        validate_year(year)
        validate_month(month)
        password = self.entries.get((batch, serial), self.default_password)
        serial_suffix = format_identifier(batch, year, month, serial)
        ssid = serial_suffix
        return {
            "batch": batch,
            "year": year,
            "month": month,
            "serial_number": serial,
            "serial": serial_suffix,
            "ssid": ssid,
            "password": password,
        }


PASSWORD_DB = PasswordDatabase(PASSWORD_DB_PATH, DEFAULT_PASSWORD)

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Flex Plus Production Flasher</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px auto; width: min(600px, calc(100vw - 32px)); max-width: 600px; padding: 0 12px; box-sizing: border-box; color: #1f2933; }
    h1 { font-size: 1.6rem; margin-bottom: 0.2rem; }
    form { display: flex; flex-direction: column; gap: 12px; margin-bottom: 18px; }
    label { font-weight: 600; font-size: 0.95rem; display: block; margin-bottom: 4px; }
    input { padding: 8px; font-size: 1rem; border: 1px solid #c0c9d2; border-radius: 4px; width: 100%; box-sizing: border-box; }
    input[readonly] { background-color: #f8fafc; }
    button { padding: 10px; font-size: 1rem; border: none; border-radius: 4px; background-color: #2563eb; color: #fff; cursor: pointer; }
    button:disabled { background-color: #9ca3af; cursor: not-allowed; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; }
    .row > div { flex: 1; min-width: 160px; }
    .status { display: flex; gap: 8px; align-items: center; font-weight: 500; margin-bottom: 10px; color: #475569; }
    .status-label { font-weight: 500; }
    .status-badge { display: inline-flex; align-items: center; gap: 8px; padding: 4px 12px; border-radius: 999px; font-size: 0.9rem; font-weight: 600; }
    .status-ready { background-color: #e0f2fe; color: #075985; }
    .status-flashing { background-color: #fef3c7; color: #92400e; }
    .status-success { background-color: #dcfce7; color: #166534; }
    .status-failed { background-color: #fee2e2; color: #b91c1c; }
    .status-spinner { width: 12px; height: 12px; border: 2px solid transparent; border-top-color: currentColor; border-left-color: currentColor; border-radius: 50%; animation: spin 0.8s linear infinite; display: none; }
    .status-flashing .status-spinner { display: inline-block; }
    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    textarea { width: 100%; height: 320px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; border: 1px solid #c0c9d2; border-radius: 4px; padding: 8px; resize: none; box-sizing: border-box; }
    .message { color: #b91c1c; min-height: 1.2rem; }
    .actions { display: flex; gap: 12px; flex-wrap: wrap; }
    .actions button { flex: none; }
  </style>
</head>
<body>
  <h1>Flex Plus Production Flasher</h1>
  <p>Provide the batch (two digits), build year/month, and inter-batch serial (001-100). Flex Plus uses a static SoftAP password (default <strong>12345678</strong> unless overridden via <code>passwords.csv</code>). Each SSID/serial becomes <strong>FP&lt;batch&gt;-&lt;year&gt;&lt;month&gt;&lt;serial&gt;</strong>.</p>
  <form id="flash-form">
    <div class="row">
      <div>
        <label for="batch">Batch number</label>
        <input id="batch" name="batch" type="number" min="1" value="1" required>
      </div>
      <div>
        <label for="year">Year (YY)</label>
        <input id="year" name="year" type="number" min="0" max="99" required>
      </div>
      <div>
        <label for="month">Month (MM)</label>
        <input id="month" name="month" type="number" min="1" max="12" required>
      </div>
    </div>
    <div class="row">
      <div>
        <label for="serialNumber">Inter-batch serial (001-100)</label>
        <input id="serialNumber" name="serialNumber" type="number" min="1" max="100" value="1" required>
      </div>
      <div style="flex:0 0 auto;align-self:flex-end;">
        <button type="button" id="next-button">Next</button>
      </div>
    </div>
    <div class="row">
      <div>
        <label for="serialSuffix">Serial suffix</label>
        <input id="serialSuffix" name="serialSuffix" readonly>
      </div>
      <div>
        <label for="ssid">SSID</label>
        <input id="ssid" name="ssid" readonly>
      </div>
      <div>
        <label for="password">Password</label>
        <input id="password" name="password" readonly>
      </div>
      <div>
        <label for="port">Serial port</label>
        <select id="port" name="port">
          <option value="">Auto</option>
        </select>
      </div>
      <div style="flex:0 0 auto;align-self:flex-end;">
        <button type="button" id="refresh-ports">Refresh ports</button>
      </div>
    </div>
    <div class="actions">
      <button id="flash-button" type="submit">Flash</button>
    </div>
    <div class="message" id="form-message"></div>
  </form>
  <div class="status">
    <span class="status-label">Status:</span>
    <span id="status" class="status-badge status-ready">
      <span class="status-spinner" aria-hidden="true"></span>
      <span id="status-text">Ready to flash</span>
    </span>
  </div>
  <div class="status" style="gap:16px;">
    <span class="status-label">Versions:</span>
    <span id="flow-version" class="status-badge status-ready" style="background:#eef2ff;color:#312e81;">Flow <span id="flow-version-text">unknown</span></span>
    <span id="bundle-version" class="status-badge status-ready" style="background:#ecfeff;color:#134e4a;">Bundle <span id="bundle-version-text">unknown</span></span>
  </div>
  <textarea id="logs" readonly placeholder="Logs will appear here..."></textarea>

  <script>
    const statusBadge = document.getElementById('status');
    const statusTextEl = document.getElementById('status-text');
    const logsEl = document.getElementById('logs');
    const messageEl = document.getElementById('form-message');
    const batchInput = document.getElementById('batch');
    const yearInput = document.getElementById('year');
    const monthInput = document.getElementById('month');
    const serialInput = document.getElementById('serialNumber');
    const serialSuffixInput = document.getElementById('serialSuffix');
    const ssidInput = document.getElementById('ssid');
    const passwordInput = document.getElementById('password');
    const portSelect = document.getElementById('port');
    const refreshPortsBtn = document.getElementById('refresh-ports');
    const form = document.getElementById('flash-form');
    const flashButton = document.getElementById('flash-button');
    const nextButton = document.getElementById('next-button');
    const flowVersionEl = document.getElementById('flow-version-text');
    const bundleVersionEl = document.getElementById('bundle-version-text');
    const SERIAL_MIN = 1;
    const SERIAL_MAX = 100;
    const STATUS_CODES = ['ready', 'flashing', 'success', 'failed'];
    let derivedReady = false;

    function updateStatus(status) {
      const fallback = { code: 'ready', message: 'Ready to flash' };
      const next = (status && typeof status === 'object') ? status : fallback;
      const code = (typeof next.code === 'string' && STATUS_CODES.includes(next.code)) ? next.code : fallback.code;
      const message = (typeof next.message === 'string' && next.message.trim().length > 0)
        ? next.message
        : fallback.message;
      statusTextEl.textContent = message;
      statusBadge.className = `status-badge status-${code}`;
      if (code === 'flashing') {
        statusBadge.setAttribute('aria-busy', 'true');
      } else {
        statusBadge.removeAttribute('aria-busy');
      }
    }

    function markDerivedDirty() {
      derivedReady = false;
      flashButton.disabled = true;
    }

    function setDefaultYearMonth() {
      const now = new Date();
      yearInput.value = String(now.getFullYear() % 100).padStart(2, '0');
      monthInput.value = String(now.getMonth() + 1).padStart(2, '0');
    }

    async function refreshState() {
      try {
        const response = await fetch('/state');
        if (!response.ok) return;
        const data = await response.json();
        updateStatus(data.status);
        const wasAtBottom = logsEl.scrollTop + logsEl.clientHeight >= logsEl.scrollHeight - 8;
        logsEl.value = data.logs;
        if (wasAtBottom) {
          logsEl.scrollTop = logsEl.scrollHeight;
        }
        flashButton.disabled = data.busy || !derivedReady;
        if (data.flow_version) {
          flowVersionEl.textContent = `${data.flow_version} (${data.flow_revision || 'unknown'})`;
        }
        if (data.manifest && data.manifest.version) {
          const built = data.manifest.built_at ? ` @ ${data.manifest.built_at}` : '';
          bundleVersionEl.textContent = `${data.manifest.version}${built}`;
        }
      } catch (err) {
        console.error('State poll failed', err);
      }
    }

    async function lookupDerived() {
      const batch = batchInput.value.trim();
      const year = yearInput.value.trim();
      const month = monthInput.value.trim();
      const serial = serialInput.value.trim();
      if (!batch || !serial || !year || !month) {
        derivedReady = false;
        flashButton.disabled = true;
        serialSuffixInput.value = '';
        ssidInput.value = '';
        passwordInput.value = '';
        return;
      }
      const yearNum = parseInt(year, 10);
      if (Number.isNaN(yearNum) || yearNum < 0 || yearNum > 99) {
        derivedReady = false;
        flashButton.disabled = true;
        messageEl.textContent = 'Year must be between 00 and 99.';
        serialSuffixInput.value = '';
        ssidInput.value = '';
        passwordInput.value = '';
        return;
      }
      const monthNum = parseInt(month, 10);
      if (Number.isNaN(monthNum) || monthNum < 1 || monthNum > 12) {
        derivedReady = false;
        flashButton.disabled = true;
        messageEl.textContent = 'Month must be between 01 and 12.';
        serialSuffixInput.value = '';
        ssidInput.value = '';
        passwordInput.value = '';
        return;
      }
      try {
        const params = new URLSearchParams({
          batch,
          serial,
          year: yearNum.toString().padStart(2, '0'),
          month: monthNum.toString().padStart(2, '0')
        });
        const response = await fetch(`/lookup?${params.toString()}`);
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          derivedReady = false;
          flashButton.disabled = true;
          messageEl.textContent = payload.error || 'Lookup failed.';
          serialSuffixInput.value = '';
          ssidInput.value = '';
          passwordInput.value = '';
          return;
        }
        derivedReady = true;
        messageEl.textContent = '';
        serialSuffixInput.value = payload.serial;
        ssidInput.value = payload.ssid;
        passwordInput.value = payload.password;
        flashButton.disabled = false;
      } catch (err) {
        derivedReady = false;
        flashButton.disabled = true;
        messageEl.textContent = 'Lookup request failed. Check the terminal for details.';
      }
    }

    function handleNext() {
      const current = parseInt(serialInput.value, 10) || SERIAL_MIN;
      if (current >= SERIAL_MAX) {
        messageEl.textContent = `Reached serial ${SERIAL_MAX}. Increase the batch number to continue.`;
        return;
      }
      serialInput.value = current + 1;
      lookupDerived();
    }

    async function startFlash(event) {
      event.preventDefault();
      if (!derivedReady) {
        messageEl.textContent = 'Lookup failed; cannot start flash.';
        return;
      }
      messageEl.textContent = '';
      flashButton.disabled = true;
      const params = new URLSearchParams();
      params.set('batch', batchInput.value.trim());
      params.set('year', yearInput.value.trim());
      params.set('month', monthInput.value.trim());
      params.set('serial', serialInput.value.trim());
       const portValue = portSelect.value;
       if (portValue && portValue !== 'auto') {
         params.set('port', portValue);
       }
      try {
        const response = await fetch('/flash', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: params.toString()
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          messageEl.textContent = payload.error || 'Unable to start flash.';
          flashButton.disabled = false;
        }
      } catch (err) {
        messageEl.textContent = 'Request failed. Check the terminal for details.';
        flashButton.disabled = false;
      }
    }

    async function refreshPorts() {
      try {
        const response = await fetch('/ports');
        if (!response.ok) return;
        const data = await response.json();
        const ports = Array.isArray(data.ports) ? data.ports : [];
        const current = portSelect.value;
        portSelect.innerHTML = '<option value=\"\">Auto</option>';
        ports.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p;
          opt.textContent = p;
          portSelect.appendChild(opt);
        });
        if (current && ports.includes(current)) {
          portSelect.value = current;
        }
      } catch (err) {
        console.error('Port refresh failed', err);
      }
    }

    form.addEventListener('submit', startFlash);
    batchInput.addEventListener('change', lookupDerived);
    serialInput.addEventListener('change', lookupDerived);
    yearInput.addEventListener('change', lookupDerived);
    monthInput.addEventListener('change', lookupDerived);
    serialInput.addEventListener('input', markDerivedDirty);
    batchInput.addEventListener('input', markDerivedDirty);
    yearInput.addEventListener('input', markDerivedDirty);
    monthInput.addEventListener('input', markDerivedDirty);
    nextButton.addEventListener('click', handleNext);
    refreshPortsBtn.addEventListener('click', refreshPorts);
    setDefaultYearMonth();
    updateStatus({ code: 'ready', message: 'Ready to flash' });
    setInterval(refreshState, 1000);
    lookupDerived();
    refreshState();
    refreshPorts();
  </script>
</body>
</html>
"""


def load_password_db() -> None:
    PASSWORD_DB.load()
    if not PASSWORD_DB.loaded_from_disk:
        print(
            f"No passwords.csv found at {PASSWORD_DB_PATH}. "
            f"Defaulting every unit to password {DEFAULT_PASSWORD}."
        )


def load_manifest_info() -> dict[str, str]:
    manifest = {"version": "unknown", "built_at": "unknown"}
    try:
        data = json.loads(MANIFEST_PATH.read_text())
        manifest["version"] = str(data.get("version", "unknown"))
        manifest["built_at"] = str(data.get("built_at", "unknown"))
    except FileNotFoundError:
        print(f"Warning: manifest.json not found at {MANIFEST_PATH}")
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: failed to parse manifest.json: {exc}")
    return manifest


def detect_flow_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(PRODUCTION_DIR), "rev-parse", "--short", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


MANIFEST_INFO = load_manifest_info()
FLOW_REVISION = detect_flow_revision()


def update_production_repo() -> None:
    commands = [
        ["git", "-C", str(PRODUCTION_DIR), "fetch", "--quiet", "--tags"],
        ["git", "-C", str(PRODUCTION_DIR), "pull", "--ff-only"],
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as exc:  # noqa: PERF203
            stderr = exc.stderr.decode("utf-8", errors="ignore").strip()
            print(f"Warning: git command failed ({' '.join(cmd)}): {stderr}")
            break


def build_flash_command(serial: str, password: str, port: str | None) -> tuple[list[str], Path]:
    system = platform.system()
    if system == "Darwin":
        script = PRODUCTION_DIR / "flash_flex_plus.sh"
        if not script.exists():
            raise FileNotFoundError(f"macOS script not found: {script}")
        cmd = ["/bin/bash", str(script), "--serial", serial, "--password", password]
        if port:
            cmd.extend(["--port", port])
        return cmd, PRODUCTION_DIR
    if system == "Windows":
        script = PRODUCTION_DIR / "flash_flex_plus.ps1"
        if not script.exists():
            raise FileNotFoundError(f"PowerShell script not found: {script}")
        shell = find_powershell()
        command = [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Serial",
            serial,
            "-Password",
            password,
        ]
        if port:
            command.extend(["-Port", port])
        return command, PRODUCTION_DIR
    raise RuntimeError(f"Unsupported operating system: {system}")


def find_powershell() -> str:
    for candidate in ("pwsh", "powershell"):
        path = shutil.which(candidate)
        if path:
            return path
    raise FileNotFoundError("Neither pwsh nor powershell was found on PATH.")


def list_serial_ports() -> list[str]:
    system = platform.system()
    ports: list[str] = []
    try:
        if system == "Darwin":
            patterns = (
                "/dev/cu.usbserial-*",
                "/dev/cu.SLAB_USB*",
                "/dev/cu.usbmodem*",
                "/dev/cu.wchusbserial*",
            )
            seen = set()
            for pat in patterns:
                for dev in glob.glob(pat):
                    if dev not in seen:
                        ports.append(dev)
                        seen.add(dev)
        elif system == "Windows":
            cmd = [
                find_powershell(),
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_SerialPort | Select-Object -ExpandProperty DeviceID",
            ]
            result = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    ports.append(line)
        else:
            # Fallback: check common Linux patterns
            for dev in glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"):
                ports.append(dev)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: failed to enumerate serial ports: {exc}")
    return ports


class FlashManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False
        self._status_code = "ready"
        self._status_message = "Ready to flash Flex Plus"
        self._logs: list[str] = []
        self._max_lines = 600

    def start(self, batch: int, year: int, month: int, serial: int, port: str | None) -> tuple[bool, str]:
        try:
            unit = PASSWORD_DB.lookup(batch, serial, year, month)
        except ValueError as exc:
            return False, str(exc)
        serial_label = str(unit["serial"])
        year_value = int(unit["year"])
        month_value = int(unit["month"])

        with self._lock:
            if self._busy:
                return False, "Flash already in progress."
            self._busy = True
            self._status_code = "flashing"
            self._status_message = f"Flashing {serial_label}..."
            self._logs = [
                f"Starting flash for batch {batch:02d} serial {serial:04d} ({year_value:02d}/{month_value:02d})",
                f"SSID: {unit['ssid']}",
            ]

        thread = threading.Thread(target=self._run_flash, args=(unit, port), daemon=True)
        thread.start()
        return True, "Flash started."

    def _append_log(self, message: str) -> None:
        sanitized = ANSI_ESCAPE.sub("", message.replace("\r", ""))
        with self._lock:
            self._logs.append(sanitized)
            if len(self._logs) > self._max_lines:
                self._logs = self._logs[-self._max_lines :]

    def _run_flash(self, unit: dict[str, object], port: str | None) -> None:
        success = False
        serial_suffix = str(unit["serial"])
        password = str(unit["password"])
        try:
            command, workdir = build_flash_command(serial_suffix, password, port)
            command_display = " ".join(shlex.quote(part) for part in command[:-1] + ["******"])
            self._append_log(f"Command: {command_display}")
            process = subprocess.Popen(
                command,
                cwd=str(workdir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                self._append_log(line.rstrip())
            success = process.wait() == 0
        except FileNotFoundError as exc:
            self._append_log(f"Error: {exc}")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Error launching flash: {exc}")
        finally:
            final_message = "Flash completed successfully." if success else "Flash failed. Check above logs."
            with self._lock:
                self._busy = False
                if success:
                    self._status_code = "success"
                    self._status_message = f"Successfully flashed {serial_suffix}."
                else:
                    self._status_code = "failed"
                    self._status_message = f"Failed flashing {serial_suffix}. Retry."
            self._append_log(final_message)

    def state(self) -> dict[str, object]:
        with self._lock:
            return {
                "status": {"code": self._status_code, "message": self._status_message},
                "busy": self._busy,
                "logs": "\n".join(self._logs),
                "manifest": MANIFEST_INFO,
                "flow_version": FLOW_VERSION,
                "flow_revision": FLOW_REVISION,
            }


class FlashRequestHandler(http.server.BaseHTTPRequestHandler):
    manager: ClassVar[FlashManager]

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send_response(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/state"):
            payload = json.dumps(self.manager.state()).encode("utf-8")
            self._send_response(200, payload, "application/json")
        elif self.path.startswith("/lookup"):
            self._handle_lookup()
        elif self.path.startswith("/ports"):
            ports = list_serial_ports()
            payload = json.dumps({"ok": True, "ports": ports}).encode("utf-8")
            self._send_response(200, payload, "application/json")
        else:
            self.send_error(404, "Not found")

    def do_POST(self) -> None:
        if self.path != "/flash":
            self.send_error(404, "Not found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        data = urllib.parse.parse_qs(body)
        try:
            batch = int(data.get("batch", [""])[0])
            year = int(data.get("year", [""])[0])
            month = int(data.get("month", [""])[0])
            serial = int(data.get("serial", [""])[0])
            port = data.get("port", [""])[0].strip()
            if not port:
                port = None
        except (TypeError, ValueError):
            self._json_response(
                {"ok": False, "error": "Batch, year, month, and serial must be integers."},
                status=400,
            )
            return

        ok, message = self.manager.start(batch, year, month, serial, port)
        status_code = 200 if ok else 400
        payload = {"ok": ok}
        if not ok:
            payload["error"] = message
        self._json_response(payload, status=status_code)

    def _handle_lookup(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        try:
            batch = int(params.get("batch", [""])[0])
            serial = int(params.get("serial", [""])[0])
            year = int(params.get("year", [""])[0])
            month = int(params.get("month", [""])[0])
            unit = PASSWORD_DB.lookup(batch, serial, year, month)
        except (ValueError, TypeError) as exc:
            self._json_response({"ok": False, "error": str(exc)}, status=400)
            return
        self._json_response({"ok": True, **unit})

    def _json_response(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_response(status, body, "application/json")

    def _send_response(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def run_server() -> None:
    update_production_repo()
    load_password_db()
    manager = FlashManager()
    FlashRequestHandler.manager = manager
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FlashRequestHandler)
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print(f"Flex Plus flasher listening on {url}")
    try:
        webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001
        print("Opening the browser failed automatically; open the URL above manually.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.shutdown()


def main() -> None:
    run_server()


if __name__ == "__main__":
    main()
