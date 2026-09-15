"""Release 打包脚本: 生成 Windows zip 与 Mac/Linux tar.gz。

用法(项目根目录): python scripts/make_release.py v2.0.0
产物输出到 上一级目录(项目根/..):
  SonNiuBi-<版本>.zip            (Windows)
  Son-NiuBi-<版本>-Mac.tar.gz    (macOS / Linux)

注意: 本脚本必须在 Windows 上也能正确产出 Mac 可用的 tar —— 关键是给
.sh/.command 文件写入 0755 执行位(Windows 文件系统没有 Unix 权限位,
直接打包会全部 644, 导致 Mac 上 ./start.sh Permission denied)。
"""
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent      # 项目根
OUT_DIR = SRC.parent.parent                        # .../Code(与历史包位置一致)
TMP = SRC / "scripts" / "_pkg_tmp"

COMMON_FILES = [
    "floating_pet.py",
    "README.md", "KNOWN_ISSUES.md", "STRATEGY_FINDINGS.md", "DEVELOPMENT.md",
    "LICENSE", "requirements.txt", "requirements-dev.txt",
    "start.bat", "start.vbs", "stop.bat", "launch_widget.vbs",
    "start.command", "stop.command", "setup.command", "start.sh", "stop.sh",
]
SCRIPTS = ["setup.bat", "setup.ps1", "run.ps1", "setup.sh", "run.sh"]
WIN_ONLY_SUFFIX = {".bat", ".vbs", ".ps1"}
MAC_ONLY_SUFFIX = {".sh", ".command"}
# tar 中需要 0755 的文件(其余 0644)
EXEC_SUFFIX = MAC_ONLY_SUFFIX


def build_stage(tmp: Path) -> Path:
    if tmp.exists():
        shutil.rmtree(tmp)
    stage = tmp / "Son NiuBi"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for d in ["app", "data", "tests"]:
        shutil.copytree(SRC / d, stage / d, ignore=ignore)
    for f in COMMON_FILES:
        shutil.copy2(SRC / f, stage / f)
    (stage / "scripts").mkdir()
    for f in SCRIPTS:
        shutil.copy2(SRC / "scripts" / f, stage / "scripts" / f)
    return stage


def make_mac(stage: Path, out: Path) -> int:
    mac = stage.parent / "mac" / "Son NiuBi"
    shutil.copytree(stage, mac)
    for p in mac.rglob("*"):
        if p.is_file() and p.suffix in WIN_ONLY_SUFFIX:
            p.unlink()
    if out.exists():
        out.unlink()
    n = 0
    with tarfile.open(out, "w:gz") as tf:
        for p in sorted(mac.rglob("*")):
            arc = p.relative_to(mac.parent)
            if p.is_dir():
                ti = tf.gettarinfo(p, arcname=str(arc))
                ti.mode = 0o755
                tf.addfile(ti)
                continue
            ti = tf.gettarinfo(p, arcname=str(arc))
            ti.mode = 0o755 if p.suffix in EXEC_SUFFIX else 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            with open(p, "rb") as fh:
                tf.addfile(ti, fh)
            n += 1
    return n


def make_win(stage: Path, out: Path) -> int:
    win = stage.parent / "win" / "Son NiuBi"
    shutil.copytree(stage, win)
    for p in list(win.rglob("*")):
        if p.is_file() and p.suffix in MAC_ONLY_SUFFIX:
            p.unlink()
    if out.exists():
        out.unlink()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(win.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(win.parent))
                n += 1
    return n


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("v"):
        sys.exit("用法: python scripts/make_release.py vX.Y.Z")
    ver = sys.argv[1]
    out_zip = OUT_DIR / f"SonNiuBi-{ver}.zip"
    out_tgz = OUT_DIR / f"Son-NiuBi-{ver}-Mac.tar.gz"

    try:
        stage = build_stage(TMP)
        n_win = make_win(stage, out_zip)
        n_mac = make_mac(stage, out_tgz)
    finally:
        if TMP.exists():
            shutil.rmtree(TMP)

    # 自检: Mac 包执行位 + 双平台关键文件
    with tarfile.open(out_tgz, "r:gz") as tf:
        modes = {m.name: m.mode for m in tf.getmembers() if m.isfile()}
        for probe in ["Son NiuBi/start.command", "Son NiuBi/start.sh",
                      "Son NiuBi/stop.sh", "Son NiuBi/scripts/setup.sh"]:
            assert modes.get(probe) == 0o755, f"{probe} 缺执行位: {modes.get(probe)}"
        assert any(m.startswith("Son NiuBi/data/") for m in modes), "缺 data/"
    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
        assert any(x.endswith("scripts\\setup.ps1") or x.endswith("scripts/setup.ps1") for x in names)
        assert any("/data/rates.db" in x or "\\data\\rates.db" in x for x in names)
        assert not any(x.endswith(".sh") for x in names), "win 包不应含 sh"

    print(f"zip: {out_zip}  {out_zip.stat().st_size/1e6:.1f} MB, {n_win} files")
    print(f"tgz: {out_tgz}  {out_tgz.stat().st_size/1e6:.1f} MB, {n_mac} files")
    print("自检通过: 执行位/关键文件/平台互斥")


if __name__ == "__main__":
    main()
