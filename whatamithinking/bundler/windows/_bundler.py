from pathlib import Path
from typing import Optional, Literal
import subprocess
import re
import sys
import os
import time

from whatamithinking.jsonproto import struct, Codec
import PyInstaller
from pyinstaller_versionfile import create_versionfile

from .._logs import logger

__all__ = [
    "BundleConfig",
    "create_bundle",
    "is_bundled",
    "get_entrypoint_dirpath",
]


_codec = Codec()


@struct
class BundleConfig:
    type: Literal["window"] | Literal["console"] | Literal["background"]
    version: str
    publisher: str
    filepath: Path
    """Python module entrypoint to start the application."""
    description: Optional[str] = None
    name: Optional[str] = None
    title: Optional[str] = None
    bitness: Optional[Literal[32] | Literal[64]] = None
    elevated: bool = False
    """True if the program should always be run with admin privileges. A prompt
    may be displayed when the program tries to start."""
    icon_filepath: Optional[Path] = None
    """Filepath to a .ico file to use for the executable file."""
    splash_filepath: Optional[Path] = None
    """Filepath to a .png image which can be displayed while the program starts up."""
    data_files: Optional[list[tuple[Path, Path]]] = None
    """Data files (images, etc.) and where to store them in the package."""
    binary_files: Optional[list[tuple[Path, Path]]] = None
    """Binary files (.dll, .so, etc.) and where to store them in the package.
    These are analyzed for dependencies and those dependencies are automatically
    included."""
    hidden_imports: Optional[list[str]] = None
    """List of packages which are required but are not directly imported
    in the app code. These should be import names not package names, which are
    sometimes different."""
    packages_with_hidden_data: Optional[list[str]] = None
    """List of packages with internal data files you want to import. Should be 
    used when your app fails to run because a data file in a package was not included."""
    bundle_mode: Literal["file", "folder"] = "folder"
    """What kind of bundle should be created for the app. file=single executable file,
    which is good for sharing with others but usually slower to start. folder=folder
    with all the app files inside, providing faster startup but less convenient to share"""

    def _post_init_(self) -> None:
        self.filepath = self.filepath.resolve()
        if self.icon_filepath:
            self.icon_filepath = self.icon_filepath.resolve()
            if self.icon_filepath.suffix != ".ico":
                raise ValueError("Icon filepath must be for a .ico image.")
        if self.splash_filepath:
            self.splash_filepath = self.splash_filepath.resolve()
            if self.splash_filepath.suffix != ".png":
                raise ValueError("Splash filepath must be for a .png image.")
        if self.name is None:
            self.name = self.filepath.stem
        if self.title is None:
            self.title = " ".join(
                [_ for _ in re.split("([A-Z][^A-Z]*)", self.name) if _]
            )
        if self.description is None:
            self.description = self.title
        if self.bitness is None:
            self.bitness = 64 if sys.maxsize > 2**32 else 32
        if self.data_files:
            for i, (src, dst) in enumerate(self.data_files):
                self.data_files[i] = (src.resolve(), dst)
        if self.binary_files:
            for i, (src, dst) in enumerate(self.binary_files):
                self.binary_files[i] = (src.resolve(), dst)


def create_bundle(
    bundle_config: BundleConfig,
    buildpath: Path = Path("./build"),
    distpath: Path = Path("./dist"),
    timeout: float = 300,
) -> None:
    """Bundle the app with all its modules into a windows-runnable executable.

    A folder in `distpath` will be created which will contain the application
    bundle.

    Antivirus and other apps on some machines can sometimes mess up writes to PE (exe) files,
    so multiple attempts may be required before we finally succeed in building it.
    If antivirus cannot be disabled for the folder you are building in, next best solution
    is to just keep trying until it works. This seems to mostly be avoided by pyinstaller
    changing the file extension at the last second, but still happens sometimes.

    Args:
        bundle_config: BundleConfig instance
        buildpath: Optional. Path to the build directory where build artifacts
            should be stored. Defaults to ./build.
        distpath: Optional. Path to the dist directory where the final output
            artifacts are stored. Defaults to ./dist.
        timeout: Optional. Number of seconds to keep trying to build the bundle
            before giving up. Defaults to 300 seconds or 5 minutes.
    """
    logger.info(f"Bundling {bundle_config.title} ({bundle_config.name})")

    _codec.execute(bundle_config, source="struct", target="struct", validate=True)

    buildpath = buildpath.resolve()
    distpath = distpath.resolve()

    vfpath = buildpath / bundle_config.name / "VERSIONINFO.txt"
    vfpath.parent.mkdir(parents=True, exist_ok=True)
    create_versionfile(
        output_file=vfpath,
        version=bundle_config.version,
        company_name=bundle_config.publisher,
        # using the title and not the description because this shows
        # up as the title of notifications in windows
        file_description=bundle_config.title,
        internal_name=bundle_config.name + ".exe",
        # this should be the name of the executable and not the script
        original_filename=bundle_config.name + ".exe",
        product_name=bundle_config.title,
    )

    cmds = [
        "pyinstaller",
        bundle_config.filepath,
        "--noconfirm",  # replace dist files without asking
        "--name",
        bundle_config.name,
        "--version-file",
        vfpath,
        "--distpath",
        distpath,
        "--workpath",
        buildpath,
        "--specpath",
        buildpath,
        "--log-level",
        "ERROR",
    ]
    if bundle_config.bundle_mode == "file":
        cmds.append("--onefile")
    if bundle_config.type == "window":
        cmds.append("--windowed")
    elif bundle_config.type == "console":
        cmds.append("--console")
    elif bundle_config.type == "background":
        cmds.extend(["--nowindowed", "--noconsole"])
    if bundle_config.elevated:
        cmds.append("--uac-admin")
    if bundle_config.icon_filepath:
        cmds.extend(["--ico", bundle_config.icon_filepath])
    if bundle_config.splash_filepath:
        cmds.extend(["--splash", bundle_config.splash_filepath])
    if bundle_config.data_files:
        for src, dst in bundle_config.data_files:
            cmds.extend(["--add-data", f"{src}{os.pathsep}{dst}"])
    if bundle_config.binary_files:
        for src, dst in bundle_config.binary_files:
            cmds.extend(["--add-binary", f"{src}{os.pathsep}{dst}"])
    if bundle_config.hidden_imports:
        for pkg in bundle_config.hidden_imports:
            cmds.extend(["--hidden-import", pkg])
    if bundle_config.packages_with_hidden_data:
        for pkg in bundle_config.packages_with_hidden_data:
            cmds.extend(["--collect-data", pkg])
    # they changed to using _internal folder for all app data. this switches back to keeping
    # everything in same folder as app
    if tuple(map(int, PyInstaller.__version__.split(".")[:3])) >= (6, 0, 0):
        cmds.extend(["--contents-directory", "."])

    deadline = time.perf_counter() + timeout
    while True:
        try:
            subprocess.run(cmds, shell=True, capture_output=True).check_returncode()
            break
        except subprocess.CalledProcessError as exc:
            if time.perf_counter() < deadline:
                # seems to be antivirus thing, i think. sporadic and cannot seem to find any
                # process using file, so just keep retrying until it works
                if (
                    "system cannot open the device or file"
                    in exc.stderr.decode().casefold()
                    or "it is being used by another process"
                    in exc.stderr.decode().casefold()
                ):
                    logger.warning(
                        "Bundling of executable failed because the file is in use. Retrying."
                    )
                    continue
            logger.exception("Bundling of executable failed: \n" + exc.stderr.decode())
            raise


def is_bundled() -> bool:
    """Return True if the program is running as a bundled/frozen app
    and False otherwise."""
    return getattr(sys, "frozen", False)


def get_entrypoint_dirpath() -> Path:
    """Call this from the bundle and it will return the path to the folder
    in which that executable is running.

    Raises:
        RuntimeError: if called when not inside a bundle/frozen app.
    """
    # pyinstaller docs: https://pyinstaller.org/en/stable/runtime-information.html
    if is_bundled() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    else:
        raise RuntimeError("App not frozen. Cannot get entrypoint dirpath.")
