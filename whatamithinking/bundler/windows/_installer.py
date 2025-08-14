from pathlib import Path
from typing import Optional, Literal, Annotated
import subprocess
import sys
import time
import shutil

from jinja2 import Environment
from whatamithinking.jsonproto import struct, Codec, DefaultFactory

from .._logs import logger

__all__ = [
    "InstallerScriptConfig",
    "ShortcutConfig",
    "InstallerConfig",
    "create_installer",
]


_codec = Codec()


@struct
class InstallerScriptConfig:
    """Config for a powershell script run during install/uninstall.

    The working directory for all scripts is the app install directory, except for
    preinstall scripts which are opened into a temporary directory. If you need to
    reference the app dir, it is recommended to pass the {bundler_app} variable
    to the script.

    Scripts should always find their supporting files relative to themselves instead
    of any static paths. The location of the scripts folder on the target machine
    may change in the future.

    Each script gets a folder where all the script and supporting files are stored,
    isolating the namespace for each script.
    """

    condition: Literal["preinstall"] | Literal["postinstall"] | Literal["preuninstall"]
    filepath: Path
    name: str
    """Unique name for the script, which should not change from one version to the next
    if the script is the same thing. This is used by the installer to skip running the 
    same scripts multiple times when apps are installed multiple times.
    
    It is recommended to use the condition string as a prefix to this name to help 
    distinguish the run conditions for each script, but this is not required.
    For example, preinstall_stopsvc and preuninstall_stopsvc.

    It is also recommended not to use spaces.
    """
    description: str
    """Just a description for documentation / auditing purposes."""
    params: str = ""
    """Params to the script.
    
    Built-in:
        {bundler_app}: this is the directory where the app program is stored
            on the target machine.
        {bundler_appdata}: this is the directory where the app data is stored
            on the target machine.
    """
    supporting_filepaths: Annotated[list[Path], DefaultFactory(list)]
    """Source filepaths to include which are used by this script. These will be stored
    in the same directory as the script file on the target machine."""

    def _post_init_(self) -> None:
        self.filepath = self.filepath.resolve()
        if not self.filepath.suffix.startswith(".ps"):
            raise ValueError("Only powershell (.ps*) installer scripts are supported.")
        if self.params:
            # do not escape for preinstall because they run in code section where
            # it is not needed
            if self.condition != "preinstall":
                self.params = self.params.replace('"', '""')
            self.params = self.params.replace("{bundler_app}", "{#bundler_app}")
            self.params = self.params.replace("{bundler_appdata}", "{code:GetDataDir}")
        for i, spath in enumerate(self.supporting_filepaths):
            self.supporting_filepaths[i] = spath.resolve()


@struct
class ShortcutConfig:
    title: str
    """String name to show to users for the shortcut."""
    target: Path
    """Filepath to the executable, relative the app install directory. The icon
    used for this exe will be used for the shortcut."""
    description: str = ""
    """App description, which is shown in the tooltip when mouse hovers."""
    params: str = ""
    """Static command-line parameters to pass to program before starting."""
    appdir: bool = True
    """True to create an shortcut in the app install directory, providing
    a place you know a shortcut will always be available."""
    desktop: bool = True
    """True to create an shortcut on the desktop."""
    startmenu: bool = True
    """True to create an shortcut in the start menu."""
    startup: bool = False
    """True to trigger this shortcut on startup. If installed with admin rights,
    the app will be started for each user when they start a session (switching users
    will not trigger again, but logout/login will); otherwise, the app will start
    each time the current user starts a session."""

    def _post_init_(self) -> None:
        self.target = Path("{app}") / self.target


@struct
class InstallerConfig:
    version: str
    """Version of the app, not the installer."""
    publisher: str
    name: str
    """Constant identifier for this package. This is used internally to keep track
    of whether this app is different from another."""
    title: str
    """This should be the pretty title/name you want to show users."""
    filename: Path
    """Installer filename with extension"""
    paths: list[tuple[Path, Path, bool]]
    """List of src, dst, recursive, with dst being the dir relative the app install dir.
    set recursive to True to include all the files inside. src can be filepath or dirpath.
    
    This can also be used for supporting files, such as for scripts so common binaries
    can be shared across scripts.
    """
    icon_filepath: Optional[Path] = None
    """Filepath to an .ico file to use for the installer executable itself."""
    bitness: Optional[Literal[32] | Literal[64]] = None
    """Bitness of apps the installer is installing. Defaults to bitness of python
    executable running this bundler."""
    shortcuts: Annotated[list[ShortcutConfig], DefaultFactory(list)]
    """Shortcuts to install on the target machine. Note that the first shortcut
    icon will be used for the uninstall icon in apps listing on OS."""
    scripts: Annotated[list[InstallerScriptConfig], DefaultFactory(list)]
    """List of powershell scripts to run at different points during install
    or uninstall."""
    elevated: bool = False
    """Should the installer require it be run as admin. If True, the installer
    will not run unless started with admin privileges. If False, the installer
    can be run both as a regular user and as a admin."""
    environment_paths: Annotated[list[Path], DefaultFactory(list)]
    """List of paths, relative the app installation directory, to add to the PATH
    environment variable, if any."""

    def _post_init_(self) -> None:
        if self.bitness is None:
            self.bitness = 64 if sys.maxsize > 2**32 else 32
        if self.environment_paths:
            for i, ep in enumerate(self.environment_paths):
                self.environment_paths[i] = Path("{app}") / ep
        if self.icon_filepath:
            self.icon_filepath = self.icon_filepath.resolve()
        for i, (src, dst, recursive) in enumerate(self.paths):
            self.paths[i] = (src.resolve(), Path("{app}") / dst, recursive)


def _build_iss(params: dict, title: str, name: str) -> str:
    logger.info(f"Building installer script {title} ({name})")
    filepath = Path(__file__).with_name("template.iss").resolve()
    env = Environment()
    with open(filepath, "r") as f:
        template = env.from_string(f.read())
    content = template.render(**params)
    return content


def _is_inno_setup_installed() -> bool:
    return shutil.which("iscc") is not None


def create_installer(
    installer_config: InstallerConfig,
    buildpath: Path = Path("./build"),
    distpath: Path = Path("./dist"),
    timeout: float = 300,
):
    """Create an installer program to automatically install the bundle
    on windows.

    Antivirus and other apps on some machines can sometimes mess up writes to PE (exe) files,
    so multiple attempts may be required before we finally succeed in building it.
    If antivirus cannot be disabled for the folder you are building in, next best solution
    is to just keep trying until it works.

    Args:
        installer_config (InstallerConfig): Installer config metadata
        buildpath (Path, optional): Directory where the build artifacts
            should be stored. Defaults to Path("./build").
        distpath (Path, optional): Directory where the output/distribution
            artifacts should be stored. Defaults to Path("./dist").
        timeout (float, Optional): How long to wait in seconds for the compilation to complete
            before giving up. Defaults to 300 seconds or 5 minutes.
    """
    logger.info(
        f"Building installer {installer_config.title} ({installer_config.name})"
    )

    if not _is_inno_setup_installed():
        raise Exception("Inno Setup (iscc) is not installed. "
                        "Please install and add iscc.exe to your env path.")

    _codec.execute(installer_config, source="struct", target="struct", validate=True)

    buildpath = buildpath.resolve()
    distpath = distpath.resolve()

    buildpath = (buildpath / installer_config.filename.stem).resolve()
    buildpath.mkdir(parents=True, exist_ok=True)

    params = dict(
        (f"installer_{k}", v)
        for k, v in _codec.execute(
            installer_config, source="struct", target="unstruct", convert=True
        ).items()
    ) | dict(installer_distpath=distpath)

    installer_script_path = buildpath / "installer.iss"
    with open(installer_script_path, "w") as f:
        f.write(_build_iss(params, installer_config.title, installer_config.name))

    logger.info(
        f"Compiling installer {installer_config.title} ({installer_config.name})"
    )
    deadline = time.perf_counter() + timeout
    has_failed = False
    while True:
        try:
            (distpath / installer_config.filename).unlink(missing_ok=True)
            subprocess.run(
                [
                    "iscc",
                    "/Q",  # quiet, except for errors, with progress
                    str(installer_script_path),
                ],
                shell=True,
                capture_output=True,
                check=True,
            )
            break
        except subprocess.CalledProcessError as exc:
            has_failed = True
            if time.perf_counter() < deadline:
                # seems to be a well-known issue with inno setup and no clear solution how to fix
                # seems like people end up using a VM to avoid whatever other windows processes are
                # screwing up access to the .iss file inno setup is using. just try until it works
                if "used by another process" in exc.stderr.decode().casefold():
                    logger.warning(
                        "Compilation of installer failed because the file is in use. Retrying."
                    )
                    continue
            logger.exception(
                "Compilation of installer failed: \n" + exc.stderr.decode()
            )
            raise
    # add final message so person watching knows it eventually worked!
    if has_failed:
        logger.info(
            f"Compiled installer {installer_config.title} ({installer_config.name})"
        )
