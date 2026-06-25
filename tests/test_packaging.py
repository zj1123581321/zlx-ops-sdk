"""打包契约:py.typed marker 必须随分发件落地。

坑2 复盘:库缺 py.typed → 下游 mypy 报 import-untyped,被迫每仓加 override。
正式库标准:打 PEP 561 py.typed marker,让下游直接吃到类型,无需 override。
本测试锁死三件事:
  1. 源码树里有 zlx_ops_sdk/py.typed
  2. pyproject 把它声明进 setuptools package-data(否则 sdist/wheel 漏掉它)
  3. 真 build 出的 wheel 里确实含 zlx_ops_sdk/py.typed(端到端证据)
"""
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parent.parent
PY_TYPED = PKG_DIR / "zlx_ops_sdk" / "py.typed"


def test_py_typed_present_in_source_tree():
    assert PY_TYPED.is_file(), (
        "缺 zlx_ops_sdk/py.typed —— 下游 mypy 会报 import-untyped。"
        "加一个空文件即可。"
    )


def test_py_typed_declared_in_package_data():
    """tomllib 解析 pyproject,确认 setuptools 会把 py.typed 打进包。

    setuptools 默认不收 .typed 这类非 .py 文件,必须显式 package-data 声明,
    否则源码树有 py.typed 但分发件里没有 —— 下游照样吃不到类型。
    """
    if sys.version_info >= (3, 11):
        import tomllib

        data = tomllib.loads((PKG_DIR / "pyproject.toml").read_text("utf-8"))
    else:  # pragma: no cover - CI/本地均 3.11+
        pytest.skip("需要 py3.11+ 的 tomllib")

    pkg_data = (
        data.get("tool", {})
        .get("setuptools", {})
        .get("package-data", {})
    )
    patterns = pkg_data.get("zlx_ops_sdk", [])
    assert "py.typed" in patterns, (
        "pyproject [tool.setuptools.package-data] 未声明 zlx_ops_sdk = ['py.typed'],"
        "py.typed 不会被打进 sdist/wheel。"
    )


def test_py_typed_ships_in_built_wheel(tmp_path):
    """端到端:真 build wheel,断言成员里有 zlx_ops_sdk/py.typed。

    这是最硬的证据 —— 前两个测试是必要条件,这个是充分条件。
    """
    try:
        import build  # noqa: F401
    except ImportError:  # pragma: no cover
        pytest.skip("build 未安装,跳过 wheel 构建端到端验证")

    out = tmp_path / "dist"
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out), str(PKG_DIR)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"wheel 构建失败:\n{proc.stdout}\n{proc.stderr}"

    wheels = list(out.glob("zlx_ops_sdk-*.whl"))
    assert wheels, f"没产出 wheel,dist={list(out.iterdir())}"
    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    assert "zlx_ops_sdk/py.typed" in names, (
        f"wheel 里没有 zlx_ops_sdk/py.typed,实际成员:{names}"
    )
