"""组装票溯（PiaoSu）Windows 免安装绿色分发包与 SHA256 清单。"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

from piaosu import PRODUCT_VERSION

ROOT = Path(__file__).resolve().parents[1]
TOP = f"piaosu-{PRODUCT_VERSION}-windows-portable"
PYTHON_EMBED_URL = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_pth() -> str:
    return "python312.zip\n.\nLib\nLib\\site-packages\n"


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def stage_launcher(dest_exe: Path) -> None:
    prebuilt = ROOT / "prebuilt_windows" / "piaosu.exe"
    if prebuilt.is_file():
        shutil.copy2(prebuilt, dest_exe)
        return
    raise FileNotFoundError("未找到 prebuilt_windows/piaosu.exe，请先用 MSVC 或 MinGW 编译启动器。")


def prepare_runtime_from_embed(runtime_dir: Path, include_ocr: bool = False) -> None:
    """从官方 Python 3.12.7 嵌入包 + pip target 安装所需依赖，生成可移动运行目录。"""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    embed_zip = ROOT / "build" / "python-3.12.7-embed-amd64.zip"
    embed_zip.parent.mkdir(parents=True, exist_ok=True)
    if not embed_zip.is_file():
        urllib.request.urlretrieve(PYTHON_EMBED_URL, str(embed_zip))
    with zipfile.ZipFile(embed_zip, "r") as zf:
        zf.extractall(runtime_dir)
    (runtime_dir / "python312._pth").write_text(runtime_pth(), encoding="ascii")

    site_pkg = runtime_dir / "Lib" / "site-packages"
    site_pkg.mkdir(parents=True, exist_ok=True)
    packages = ["openpyxl>=3.1", "pymupdf>=1.23", "pypdf>=4.0"]
    if include_ocr:
        packages.extend(["rapidocr-onnxruntime>=1.3", "Pillow>=10.0"])
    subprocess.run(
        [
            "python",
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "--upgrade",
            "--target",
            str(site_pkg),
            *packages,
        ],
        check=True,
    )


def assemble_portable_zip(
    dest_zip: Path,
    *,
    runtime_dir: Path | None = None,
    include_ocr: bool = False,
) -> list[str]:
    work = dest_zip.parent / (dest_zip.stem + "-stage")
    if work.exists():
        shutil.rmtree(work)
    stage = work / TOP
    stage.mkdir(parents=True)
    try:
        stage_launcher(stage / "piaosu.exe")

        target_runtime = stage / "runtime"
        if runtime_dir is not None and runtime_dir.is_dir():
            _copy_tree(runtime_dir, target_runtime)
            (target_runtime / "python312._pth").write_text(runtime_pth(), encoding="ascii")
        else:
            prepare_runtime_from_embed(target_runtime, include_ocr=include_ocr)

        # 拷贝 piaosu 源码包到 runtime/piaosu
        _copy_tree(ROOT / "piaosu", target_runtime / "piaosu")

        # 拷贝示例模板、文档与协议
        if (ROOT / "examples").is_dir():
            _copy_tree(ROOT / "examples", stage / "examples")
        for doc in ("LICENSE", "README.md"):
            if (ROOT / doc).is_file():
                shutil.copy2(ROOT / doc, stage / doc)

        (stage / "使用说明.txt").write_text(
            f"票溯 (PiaoSu) {PRODUCT_VERSION} Windows 免安装绿色版\n"
            "==================================================\n"
            "1. 解压整个文件夹到任意本地路径，保持目录结构完整。\n"
            "2. 双击 piaosu.exe 即可启动纯离线桌面报销界面，无需额外安装 Python。\n"
            "3. 支持数电票 XML、国标 OFD、矢量 PDF 双通道解析与查重台账管理。\n"
            "4. examples/ 目录包含示例企业报销单模板，可在主界面点击“按企业模板填表”直接体验。\n"
            "5. 本软件 100% 本地离线运行，不上传任何发票或财务数据（MIT 开源协议）。\n",
            encoding="utf-8",
        )

        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        names: list[str] = []
        with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(stage.rglob("*")):
                if not path.is_file():
                    continue
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                arcname = str(path.relative_to(work)).replace("\\", "/")
                zf.write(path, arcname)
                names.append(arcname)

        sha_file = dest_zip.parent / "SHA256.txt"
        sha_file.write_text(f"{sha256_file(dest_zip)}  {dest_zip.name}\n", encoding="utf-8")
        return names
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建票溯 Windows 免安装绿色发行包")
    parser.add_argument("--runtime-dir", default=None, help="预先准备好的 Python 3.12 嵌入式运行目录")
    parser.add_argument("--include-ocr", action="store_true", help="将 rapidocr-onnxruntime 打入绿色包")
    parser.add_argument("--out", default=None, help="输出 zip 路径")
    args = parser.parse_args()

    out_zip = Path(args.out) if args.out else (ROOT / "dist" / f"{TOP}.zip")
    rt = Path(args.runtime_dir) if args.runtime_dir else None
    names = assemble_portable_zip(out_zip, runtime_dir=rt, include_ocr=args.include_ocr)
    print(f"wrote {out_zip} ({len(names)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
