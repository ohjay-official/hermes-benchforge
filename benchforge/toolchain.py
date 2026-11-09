"""Find or install what BenchForge runs: Node.js (for the chip simulator) and arduino-cli (the compiler).

Everything BenchForge installs lives in one folder it owns (``<root>``, normally
``<HERMES_HOME>/plugin-data/benchforge/toolchain``):

    arduino-cli/<version>/arduino-cli     the official binary from github.com/arduino/arduino-cli,
                                          checked against the SHA-256 pinned below before it is unpacked
    arduino-cli.yaml                      a config that keeps arduino-cli's data inside <root>
    arduino15/                            the AVR core (arduino:avr) and its avr-gcc, installed by
                                          arduino-cli from Arduino's own package index (it checks each
                                          download against the index's checksums)
    build-cache/                          compiled Arduino core, reused between builds

Nothing is installed until a person runs ``hermes bench setup`` (or ``python -m benchforge setup``).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ARDUINO_CLI_VERSION = "1.5.1"
ARDUINO_CLI_SHA256 = {
    "Linux_64bit.tar.gz": "28a8e119c498a25607821c36cb2dc49e8463941b261a0d99091baa7bc692dd2b",
    "Linux_ARM64.tar.gz": "1e69e077479f300614d4551334e0a33f08ee40b04315d83b8e7e0e94f0d0ee62",
    "Linux_ARMv7.tar.gz": "890af36e9873606e4dfa743534846186621b9f3a339175ea3ec481adecf07143",
    "macOS_64bit.tar.gz": "c982e940027996bea9901050e95fae99c59c1dcfee54beedecaf28141e7bf2e7",
    "macOS_ARM64.tar.gz": "cb952e8c1621c95ef5f1d17831c945e3d0ec5973f89c557a7ec8feb9c4f7d4c9",
    "Windows_64bit.zip": "fabe42e0eb04d00e776a66178299ff95a46c623dbc260f997e58fd514853dd40",
}
AVR_CORE = "arduino:avr@1.8.6"
LIBRARIES = ("Servo@1.2.2",)
FQBN = "arduino:avr:uno"
DOWNLOAD_LIMIT = 80 * 1024 * 1024


class ToolchainError(RuntimeError):
    pass


def _asset_suffix() -> str:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Linux":
        if machine in ("x86_64", "amd64"):
            return "Linux_64bit.tar.gz"
        if machine in ("aarch64", "arm64"):
            return "Linux_ARM64.tar.gz"
        if machine.startswith("armv7"):
            return "Linux_ARMv7.tar.gz"
    if system == "Darwin":
        return "macOS_ARM64.tar.gz" if machine == "arm64" else "macOS_64bit.tar.gz"
    if system == "Windows" and machine in ("amd64", "x86_64"):
        return "Windows_64bit.zip"
    raise ToolchainError(f"arduino-cli has no official build for {system} {machine}")


@dataclass
class Toolchain:
    root: Path

    @property
    def cli_dir(self) -> Path:
        return self.root / "arduino-cli" / ARDUINO_CLI_VERSION

    @property
    def config(self) -> Path:
        return self.root / "arduino-cli.yaml"

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    def arduino_cli(self) -> Path | None:
        name = "arduino-cli.exe" if os.name == "nt" else "arduino-cli"
        path = self.cli_dir / name
        return path if path.is_file() and os.access(path, os.X_OK) else None

    def state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def node(self) -> str | None:
        """The Node.js setup recorded, else one found now (never installs anything)."""
        recorded = self.state().get("node")
        if recorded and os.path.isfile(recorded) and os.access(recorded, os.X_OK):
            if not any(os.environ.get(k) for k in ("BENCHFORGE_NODE", "HERMES_NODE")):
                return recorded
        return find_node()

    def ready(self) -> bool:
        st = self.state()
        return (self.arduino_cli() is not None and self.config.is_file()
                and st.get("core") == AVR_CORE and st.get("cli") == ARDUINO_CLI_VERSION)

    # ------------------------------------------------------------ install

    def write_config(self) -> None:
        q = lambda p: json.dumps(str(p))            # a JSON string is a valid YAML scalar
        self.root.mkdir(parents=True, exist_ok=True)
        self.config.write_text(
            "# Written by BenchForge. Keeps arduino-cli's data inside BenchForge's own folder.\n"
            "directories:\n"
            f"  data: {q(self.root / 'arduino15')}\n"
            f"  downloads: {q(self.root / 'staging')}\n"
            f"  user: {q(self.root / 'sketchbook')}\n"
            "board_manager:\n  additional_urls: []\n"
            "library:\n  enable_unsafe_install: false\n"
            "updater:\n  enable_notification: false\n"
            f"build_cache:\n  path: {q(self.root / 'build-cache')}\n",
            encoding="utf-8")

    def env(self) -> dict:
        """A small, fixed environment for arduino-cli: none of the user's variables or secrets."""
        home = self.root / "home"
        home.mkdir(parents=True, exist_ok=True)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "LANG": "C", "LC_ALL": "C",
               "ARDUINO_UPDATER_ENABLE_NOTIFICATION": "false"}
        for k in ("SYSTEMROOT", "TEMP", "TMP", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                  "https_proxy", "http_proxy", "no_proxy", "SSL_CERT_FILE"):
            if k in os.environ:
                env[k] = os.environ[k]
        return env

    def run_cli(self, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
        cli = self.arduino_cli()
        if cli is None:
            raise ToolchainError("arduino-cli is not installed; run `hermes bench setup`")
        return subprocess.run([str(cli), *args, "--config-file", str(self.config), "--no-color"],
                              capture_output=True, text=True, timeout=timeout, env=self.env(),
                              cwd=str(self.root))

    def install(self, log=print) -> dict:
        """Find Node.js, download and verify arduino-cli, then install the AVR core and libraries.
        Safe to re-run: finished parts are kept."""
        self.root.mkdir(parents=True, exist_ok=True)
        node = find_node(install=True)
        if not node or node_version(node)[:1] < (18,):
            raise ToolchainError("BenchForge needs Node.js 18 or newer for its chip simulator. "
                                 "Run `hermes pm install node`, or install it from nodejs.org, then retry.")
        log(f"Node.js {'.'.join(map(str, node_version(node)))} found")
        if self.arduino_cli() is None:
            suffix = _asset_suffix()
            name = f"arduino-cli_{ARDUINO_CLI_VERSION}_{suffix}"
            url = f"https://github.com/arduino/arduino-cli/releases/download/v{ARDUINO_CLI_VERSION}/{name}"
            log(f"Downloading {name} from github.com/arduino/arduino-cli")
            data = _download(url)
            got = hashlib.sha256(data).hexdigest()
            if got != ARDUINO_CLI_SHA256[suffix]:
                raise ToolchainError(f"{name} failed its checksum (got {got}); nothing was installed")
            _extract_binary(data, suffix, self.cli_dir)
            log(f"arduino-cli {ARDUINO_CLI_VERSION} verified (sha256 {got[:12]}…)")
        self.write_config()
        for args, what in ((("core", "update-index"), "Arduino package index"),
                           (("core", "install", AVR_CORE), f"AVR core {AVR_CORE} (with avr-gcc)"),
                           *((("lib", "install", lib), f"library {lib}") for lib in LIBRARIES)):
            log(f"Installing {what}")
            p = self.run_cli(*args)
            if p.returncode != 0:
                raise ToolchainError(f"arduino-cli {' '.join(args)} failed: {(p.stderr or p.stdout).strip()[-800:]}")
        state = {"cli": ARDUINO_CLI_VERSION, "core": AVR_CORE, "libraries": list(LIBRARIES), "node": node}
        self.state_file.write_text(json.dumps(state, indent=2))
        return state


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "benchforge-setup"})
    with urllib.request.urlopen(req, timeout=120) as resp:   # noqa: S310 (fixed https URL)
        if not resp.geturl().startswith("https://"):
            raise ToolchainError("download was redirected away from https")
        buf = io.BytesIO()
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            buf.write(chunk)
            if buf.tell() > DOWNLOAD_LIMIT:
                raise ToolchainError("download is larger than expected; stopped")
        return buf.getvalue()


def _extract_binary(data: bytes, suffix: str, dest: Path) -> Path:
    """Unpack only the arduino-cli executable, by name, never trusting paths inside the archive."""
    exe = "arduino-cli.exe" if suffix.endswith(".zip") else "arduino-cli"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / exe
    if suffix.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next((m for m in z.infolist() if m.filename == exe), None)
            if member is None:
                raise ToolchainError("arduino-cli.exe not found in the archive")
            target.write_bytes(z.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            member = next((m for m in t.getmembers() if m.name in (exe, "./" + exe) and m.isfile()), None)
            if member is None:
                raise ToolchainError("arduino-cli not found in the archive")
            f = t.extractfile(member)
            target.write_bytes(f.read())
    target.chmod(0o755)
    return target


# ---------------------------------------------------------------- Node.js

def find_node(install: bool = False) -> str | None:
    """Node.js for the simulator: BENCHFORGE_NODE or HERMES_NODE, then Node on PATH, then (only when
    ``install`` is true, i.e. during setup) the Node runtime Hermes's package manager provides."""
    for key in ("BENCHFORGE_NODE", "HERMES_NODE"):
        p = os.environ.get(key)
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    path = shutil.which("node")
    if path or not install:
        return path
    try:
        import pm  # Hermes's package manager (public plugin API); explicit=True: the person ran setup

        return shutil.which("node", path=pm.ensure("node", explicit=True).env["PATH"])
    except Exception:
        return None


def node_version(node: str) -> tuple:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        return tuple(int(x) for x in out.lstrip("v").split(".")[:3])
    except (OSError, ValueError, subprocess.SubprocessError):
        return ()
