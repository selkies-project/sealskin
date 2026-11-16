"""The Bakery's catalog: what the guided steps offer by name, per distribution family.

Each table maps a choice of a step to the packages, addresses, and paths it
stands for on each family of `bakery.FAMILIES` (`alpine`, `arch`, `debian`,
`fedora`). A family a choice has no entry for is one the editor does not
offer it on, and `bakery` refuses to render it there. `bakery` renders the
steps; `offers` is what the editor reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The `ID` of `os-release` on the images a personal package archive installs on.
PPA_DISTROS = ("ubuntu",)

#: Named sets of packages the package step adds in one click.
BUNDLES: dict[str, dict[str, tuple[str, ...]]] = {
    "companions": {
        "alpine": ("galculator", "mousepad", "ristretto", "xarchiver"),
        "arch": ("galculator", "mousepad", "ristretto", "xarchiver"),
        "debian": ("galculator", "mousepad", "ristretto", "xarchiver"),
        "fedora": ("galculator", "mousepad", "ristretto", "xarchiver"),
    },
    "shell": {
        "alpine": ("git", "htop", "less", "neovim", "tmux", "zsh"),
        "arch": ("git", "htop", "less", "neovim", "tmux", "zsh"),
        "debian": ("git", "htop", "less", "neovim", "tmux", "zsh"),
        "fedora": ("git", "htop", "less", "neovim", "tmux", "zsh"),
    },
    "build": {
        "alpine": ("build-base", "cmake", "git", "pkgconf"),
        "arch": ("base-devel", "cmake", "git"),
        "debian": ("build-essential", "cmake", "git", "pkg-config"),
        "fedora": ("cmake", "gcc", "gcc-c++", "git", "make", "pkgconf-pkg-config"),
    },
    "archives": {
        "alpine": ("7zip", "unzip", "xz", "zip"),
        "arch": ("7zip", "unrar", "unzip", "xz", "zip"),
        "debian": ("7zip", "unrar-free", "unzip", "xz-utils", "zip"),
        "fedora": ("7zip", "unzip", "xz", "zip"),
    },
}


@dataclass(frozen=True)
class FontPack:
    """A set of fonts a distribution packages.

    Attributes:
        packages: The packages per family.
        before: Commands run before the install, per family.
        after: Commands run after it, per family.
        reinstall: Install the packages over the copy the image holds.
        eula: The pack has a license the administrator accepts in the step.
    """

    packages: dict[str, tuple[str, ...]]
    before: dict[str, tuple[str, ...]] = field(default_factory=dict)
    after: dict[str, tuple[str, ...]] = field(default_factory=dict)
    reinstall: bool = False
    eula: bool = False


FONT_PACKS: dict[str, FontPack] = {
    "office": FontPack(
        packages={
            "alpine": ("font-carlito", "font-liberation"),
            "arch": ("ttf-caladea", "ttf-carlito", "ttf-liberation"),
            "debian": ("fonts-crosextra-caladea", "fonts-crosextra-carlito", "fonts-liberation"),
            "fedora": ("google-carlito-fonts", "google-crosextra-caladea-fonts", "liberation-fonts-all"),
        }
    ),
    "cjk": FontPack(
        packages={
            "alpine": ("font-noto-cjk", "font-noto-cjk-extra"),
            "arch": ("noto-fonts-cjk",),
            "debian": ("fonts-noto-cjk", "fonts-noto-cjk-extra"),
            "fedora": ("google-noto-sans-cjk-fonts", "google-noto-serif-cjk-fonts"),
        },
        # The base image keeps the serif and bold CJK files out of its install.
        before={"debian": ("rm -f /etc/dpkg/dpkg.cfg.d/selkies-fonts",)},
        reinstall=True,
    ),
    "noto": FontPack(
        packages={
            "alpine": ("font-noto", "font-noto-extra"),
            "arch": ("noto-fonts", "noto-fonts-extra"),
            "debian": ("fonts-noto-extra", "fonts-noto-ui-core"),
            "fedora": ("google-noto-sans-fonts", "google-noto-serif-fonts"),
        }
    ),
    "microsoft": FontPack(
        packages={"alpine": ("msttcorefonts-installer",), "debian": ("ttf-mscorefonts-installer",)},
        before={
            "debian": ('echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" | debconf-set-selections',)
        },
        after={"alpine": ("update-ms-fonts",)},
        eula=True,
    ),
}


@dataclass(frozen=True)
class DevKit:
    """A language's toolchain and how its own packages are installed outside the home directory.

    Attributes:
        packages: The toolchain's packages per family.
        completes: Per family, a program and the packages installed when the toolchain's own did not bring it.
        install: The command that installs the language's packages, their names following it; empty for a kit with none.
        before: Commands run before `install`.
        leftovers: Paths removed after it.
        env: Variables the image keeps, as `(name, value)`.
        pattern: What one of the language's package names looks like.
    """

    packages: dict[str, tuple[str, ...]]
    completes: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    install: str = ""
    before: tuple[str, ...] = ()
    leftovers: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    pattern: str = r"^[A-Za-z0-9@][A-Za-z0-9+._:@=~<>!/\[\],-]*$"


#: The virtual environment a Python kit's packages are installed in; its `bin` goes last on `PATH`.
PYTHON_VENV = "/opt/venv"

DEV_KITS: dict[str, DevKit] = {
    "python": DevKit(
        packages={
            "alpine": ("py3-pip", "python3", "python3-dev"),
            "arch": ("python", "python-pip"),
            "debian": ("python3", "python3-dev", "python3-pip", "python3-venv"),
            "fedora": ("python3", "python3-devel", "python3-pip"),
        },
        before=(f"python3 -m venv --system-site-packages {PYTHON_VENV}",),
        install=f"{PYTHON_VENV}/bin/pip install --no-cache-dir",
        leftovers=("/root/.cache",),
        env=(("PATH", f"${{PATH}}:{PYTHON_VENV}/bin"),),
    ),
    "node": DevKit(
        packages={
            "alpine": ("nodejs", "npm"),
            "arch": ("nodejs", "npm"),
            "debian": ("nodejs",),
            "fedora": ("nodejs", "nodejs-npm"),
        },
        # Node from its vendor's repository, which a base image may name, brings npm and conflicts with the distribution's.
        completes={"debian": ("npm", ("npm",))},
        install="npm install -g --prefix /usr/local",
        leftovers=("/root/.npm",),
    ),
    "rust": DevKit(
        packages={
            "alpine": ("build-base", "cargo"),
            "arch": ("base-devel", "rust"),
            "debian": ("build-essential", "cargo"),
            "fedora": ("cargo", "gcc"),
        },
        install="CARGO_HOME=/tmp/cargo cargo install --root /usr/local",
        leftovers=("/tmp/cargo",),
    ),
    "go": DevKit(
        packages={"alpine": ("go",), "arch": ("go",), "debian": ("golang",), "fedora": ("golang",)},
        install="GOBIN=/usr/local/bin GOPATH=/tmp/go GOCACHE=/tmp/go-cache GOFLAGS=-modcacherw go install",
        leftovers=("/tmp/go", "/tmp/go-cache"),
    ),
    "java": DevKit(
        packages={
            "alpine": ("openjdk21",),
            "arch": ("jdk-openjdk",),
            "debian": ("default-jdk",),
            "fedora": ("java-latest-openjdk-devel",),
        }
    ),
}


@dataclass(frozen=True)
class Repository:
    """An APT repository a vendor publishes its application in.

    Attributes:
        key_url: Where the signing key is fetched from.
        key_file: Where it is kept; `.asc` for an armored key, `.gpg` for a binary one.
        source: The line of the `sources.list.d` file, `{key}` standing for `key_file`.
        pin: The origin whose packages win over the distribution's, or empty.
    """

    key_url: str
    key_file: str
    source: str
    pin: str = ""


@dataclass(frozen=True)
class App:
    """An application the curated step installs.

    Attributes:
        name: The name the editor shows.
        packages: The packages per family, from the distribution or from `repository`.
        repository: The vendor's APT repository per `os-release` `ID`; a distribution with an entry installs from it.
        ppa: The personal package archive per `os-release` `ID` the packages come from there.
        command: The program that starts it, which its desktop entries name.
        commands: The program on the families that name it differently.
        sandbox: The program needs `--no-sandbox` inside a container.
        browser: The application opens web links.
    """

    name: str
    packages: dict[str, tuple[str, ...]]
    command: str
    repository: dict[str, Repository] = field(default_factory=dict)
    ppa: dict[str, str] = field(default_factory=dict)
    commands: dict[str, str] = field(default_factory=dict)
    sandbox: bool = False
    browser: bool = False

    def offered(self, family: str, distro: str) -> bool:
        """Whether the application installs on a family, a vendor's repository only on the distributions it names."""
        if family not in self.packages:
            return False
        return not self.repository or distro in self.repository or family != "debian"


_MOZILLA = Repository(
    key_url="https://packages.mozilla.org/apt/repo-signing-key.gpg",
    key_file="/etc/apt/keyrings/packages.mozilla.org.asc",
    source="deb [signed-by={key}] https://packages.mozilla.org/apt mozilla main",
    pin="packages.mozilla.org",
)
_GOOGLE = Repository(
    key_url="https://dl.google.com/linux/linux_signing_key.pub",
    key_file="/etc/apt/keyrings/google-chrome.asc",
    source="deb [arch=amd64 signed-by={key}] https://dl.google.com/linux/chrome/deb/ stable main",
)
_BRAVE = Repository(
    key_url="https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg",
    key_file="/etc/apt/keyrings/brave-browser.gpg",
    source="deb [signed-by={key}] https://brave-browser-apt-release.s3.brave.com/ stable main",
)
_MICROSOFT = Repository(
    key_url="https://packages.microsoft.com/keys/microsoft.asc",
    key_file="/etc/apt/keyrings/microsoft.asc",
    source="deb [arch=amd64,arm64,armhf signed-by={key}] https://packages.microsoft.com/repos/code stable main",
)

APPS: dict[str, App] = {
    "chromium": App(
        name="Chromium",
        packages={"alpine": ("chromium",), "arch": ("chromium",), "debian": ("chromium",), "fedora": ("chromium",)},
        ppa={"ubuntu": "xtradeb/apps"},
        command="chromium",
        commands={"fedora": "chromium-browser"},
        sandbox=True,
        browser=True,
    ),
    "firefox": App(
        name="Firefox",
        packages={"alpine": ("firefox",), "arch": ("firefox",), "debian": ("firefox",), "fedora": ("firefox",)},
        repository={"debian": _MOZILLA, "ubuntu": _MOZILLA, "kali": _MOZILLA},
        command="firefox",
        browser=True,
    ),
    "chrome": App(
        name="Google Chrome",
        packages={"debian": ("google-chrome-stable",)},
        repository={"debian": _GOOGLE, "ubuntu": _GOOGLE, "kali": _GOOGLE},
        command="google-chrome-stable",
        sandbox=True,
        browser=True,
    ),
    "brave": App(
        name="Brave",
        packages={"debian": ("brave-browser",)},
        repository={"debian": _BRAVE, "ubuntu": _BRAVE, "kali": _BRAVE},
        command="brave-browser-stable",
        sandbox=True,
        browser=True,
    ),
    "vscode": App(
        name="Visual Studio Code",
        packages={"debian": ("code",)},
        repository={"debian": _MICROSOFT, "ubuntu": _MICROSOFT, "kali": _MICROSOFT},
        command="code",
        sandbox=True,
    ),
}

#: The menu a desktop entry is listed under.
DESKTOP_CATEGORIES = ("Utility", "Development", "Graphics", "Network", "Office", "AudioVideo", "Game", "Education", "System")



def offers(family: str, distro: str) -> dict[str, Any]:
    """Return what the guided steps offer on an image of a family and an `os-release` `ID`.

    Returns:
        `bundles` (name to packages), `fonts` (name to whether the pack has a
        license to accept), `devkits` (name to whether the kit installs
        packages of its language), `apps` (id to `name` and `browser`),
        `categories`, and `hidden`, the kinds of step the image cannot take.
    """
    return {
        "bundles": {name: list(packs[family]) for name, packs in BUNDLES.items() if family in packs},
        "fonts": {name: pack.eula for name, pack in FONT_PACKS.items() if family in pack.packages},
        "devkits": {name: bool(kit.install) for name, kit in DEV_KITS.items() if family in kit.packages},
        "apps": {name: {"name": app.name, "browser": app.browser} for name, app in APPS.items() if app.offered(family, distro)},
        "categories": list(DESKTOP_CATEGORIES),
        "hidden": [] if distro in PPA_DISTROS else ["ppa"],
    }
