# PyInstaller spec for FacadeKit. Run from anywhere:
#   pyinstaller packaging/facadekit.spec
# Requires the web UI to be built into studio/backend/src/facadekit/webdist first.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is injected by PyInstaller)
SRC = ROOT / "studio" / "backend" / "src"
PKG = SRC / "facadekit"

webdist = PKG / "webdist"
if not (webdist / "index.html").exists():
    raise SystemExit(f"Web UI not built: {webdist / 'index.html'} is missing. Run `pnpm build`.")

datas = [
    (str(webdist), "facadekit/webdist"),
    (str(PKG / "ai" / "model_registry.json"), "facadekit/ai"),
    (str(ROOT / "LICENSE"), "."),
]
notices = ROOT / "THIRD_PARTY_NOTICES.txt"  # generated in CI; optional locally
if notices.exists():
    datas.append((str(notices), "."))
datas += collect_data_files("imageio_ffmpeg", include_py_files=False)
binaries = collect_dynamic_libs("imageio_ffmpeg")

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "onnxruntime",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["librosa", "numba", "llvmlite", "tkinter"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FacadeKit",
    console=True,  # users need to see the URL and errors
    upx=False,
)
coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.datas, name="FacadeKit", upx=False
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="FacadeKit.app",
        bundle_identifier="org.facadekit.studio",
        info_plist={
            "CFBundleName": "FacadeKit",
            "CFBundleDisplayName": "FacadeKit",
            "NSCameraUsageDescription": "FacadeKit uses the camera to scan your projection.",
            "NSHighResolutionCapable": True,
        },
    )
