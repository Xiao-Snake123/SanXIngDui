"""本机 PostgreSQL + pgvector 一键初始化（Windows 免安装发行版）。

为什么写这个脚本
----------------
「我在本机装好了」和「别人 clone 下来能复现」是两件事。
这个项目要求每个依赖都能一条命令跑起来，数据库也不该例外 ——
否则 README 上那句「接入 PostgreSQL」对新接手的人就是空话。

脚本做四件事，**全部幂等**（重复执行安全）：
  1. 找（或下载）PostgreSQL 免安装二进制包；
  2. 装 pgvector 扩展文件（Windows 没有编译环境，用预编译包）；
  3. initdb + 启动服务；
  4. 建角色 / 建库 / CREATE EXTENSION vector。

依赖的外部资源：
  PostgreSQL  : https://get.enterprisedb.com/postgresql/postgresql-<ver>-windows-x64-binaries.zip
  pgvector    : https://github.com/andreiramani/pgvector_pgsql_windows （预编译 pg13~pg18）

用法：
    python scripts/setup_postgres.py                 # 用默认路径与端口
    python scripts/setup_postgres.py --pg-version 17.6 --port 5432
    python scripts/setup_postgres.py --print-dsn     # 只打印 DSN，不改动任何东西

已有 PostgreSQL 的用户：把 PG_HOME / PGDATA 指过去即可，脚本会跳过下载，
只做「建角色 / 建库 / 装扩展」这几步。
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DEFAULT_PG_VERSION = "17.6"
ROLE = "sxd"
PASSWORD = "sxd_app_2026"
DATABASE = "sanxingdui"

WINDOWS = platform.system() == "Windows"
DEFAULT_PG_HOME = Path("D:/develop/pgsql") if WINDOWS else Path("/usr/lib/postgresql/17")
DEFAULT_PGDATA = Path("D:/develop/pgdata") if WINDOWS else Path("/var/lib/postgresql/17/main")


def log(message: str) -> None:
    print(message, flush=True)


def run(binary: Path | str, *args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """执行外部命令，回显成一行 —— 出问题时能直接看到跑的是什么。"""
    command = [str(binary), *args]
    log("  $ " + " ".join(command))
    return subprocess.run(command, check=check, capture_output=True, text=True, env=env)


# ── 1. 二进制 ────────────────────────────────────────────────────────────────
def ensure_postgres(pg_home: Path, version: str) -> None:
    if (pg_home / "bin" / ("pg_ctl.exe" if WINDOWS else "pg_ctl")).exists():
        log(f"[1/4] PostgreSQL 已存在，跳过下载: {pg_home}")
        return
    if not WINDOWS:
        log("[1/4] 非 Windows 平台请用系统包管理器安装 postgresql（含 pgvector），本脚本跳过下载")
        return

    url = f"https://get.enterprisedb.com/postgresql/postgresql-{version}-1-windows-x64-binaries.zip"
    archive = pg_home.parent / f"postgresql-{version}-windows-x64-binaries.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    log(f"[1/4] 下载 PostgreSQL {version}（约 315 MB）：{url}")
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)  # noqa: S310 - 固定 HTTPS 官方源
    log(f"  解压到 {pg_home.parent} ...")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(pg_home.parent)
    log("  完成")


# ── 2. pgvector ──────────────────────────────────────────────────────────────
def ensure_pgvector(pg_home: Path, version: str) -> None:
    """把预编译的 vector.dll / sql / control 放进 PostgreSQL 目录。

    Windows 上没有 MSVC 就编译不了扩展，所以用社区预编译包。它在 PG13~PG18
    上都有产物，我们按大版本选。
    """
    marker = pg_home / "share" / "extension" / "vector.control"
    if marker.exists():
        log("[2/4] pgvector 已安装，跳过")
        return
    if not WINDOWS:
        log("[2/4] 非 Windows：请用包管理器安装 pgvector（如 apt install postgresql-17-pgvector）")
        return

    major = version.split(".")[0]
    url = (
        "https://github.com/andreiramani/pgvector_pgsql_windows/releases/download/"
        f"0.8.6_{major}/vector.v0.8.6-pg{major}.zip"
    )
    archive = pg_home.parent / f"pgvector-pg{major}.zip"
    log(f"[2/4] 下载 pgvector（pg{major}）：{url}")
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)  # noqa: S310

    staging = pg_home.parent / f"_pgvector-pg{major}"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(staging)
    shutil.copy2(staging / "lib" / "vector.dll", pg_home / "lib" / "vector.dll")
    target_include = pg_home / "include" / "server" / "extension" / "vector"
    target_include.mkdir(parents=True, exist_ok=True)
    for item in (staging / "include" / "server" / "extension" / "vector").glob("*"):
        shutil.copy2(item, target_include / item.name)
    for item in (staging / "share" / "extension").glob("vector*"):
        shutil.copy2(item, pg_home / "share" / "extension" / item.name)
    log("  完成")


# ── 3. 实例 ──────────────────────────────────────────────────────────────────
def ensure_instance(pg_home: Path, pgdata: Path, port: int) -> None:
    bin_dir = pg_home / "bin"
    initdb = bin_dir / ("initdb.exe" if WINDOWS else "initdb")
    password_file = pgdata.parent / ".pg_super_password"
    if not (pgdata / "PG_VERSION").exists():
        log(f"[3/4] 初始化数据目录: {pgdata}")
        password_file.write_text("sxd_pg_2026", encoding="utf-8")
        pgdata.parent.mkdir(parents=True, exist_ok=True)
        run(
            initdb,
            "-D", str(pgdata),
            "-U", "postgres",
            "-E", "UTF8",
            "--locale=C",
            "--auth-host=scram-sha-256",
            "--auth-local=trust",
            f"--pwfile={password_file}",
        )
        password_file.unlink(missing_ok=True)
    else:
        log(f"[3/4] 数据目录已存在: {pgdata}")

    pg_ctl = bin_dir / ("pg_ctl.exe" if WINDOWS else "pg_ctl")
    status = run(pg_ctl, "-D", str(pgdata), "status", check=False)
    if status.returncode == 0:
        log("  服务已在运行")
        return
    log(f"  启动服务（端口 {port}，仅监听 127.0.0.1）")
    run(
        pg_ctl,
        "-D", str(pgdata),
        "-l", str(pgdata / "server.log"),
        "-o", f"-p {port} -c listen_addresses=127.0.0.1",
        "start",
    )


# ── 4. 角色 / 库 / 扩展 ──────────────────────────────────────────────────────
def ensure_objects(pg_home: Path, port: int) -> None:
    psql = pg_home / "bin" / ("psql.exe" if WINDOWS else "psql")
    base = ["-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "-v", "ON_ERROR_STOP=0"]

    log(f"[4/4] 建角色 {ROLE} / 建库 {DATABASE} / 启用 pgvector")
    env = {**os.environ, "PGPASSWORD": "sxd_pg_2026"}
    run(psql, *base, "-c", f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}' CREATEDB;", check=False, env=env)
    run(psql, *base, "-c", f"CREATE DATABASE {DATABASE} OWNER {ROLE} ENCODING 'UTF8';", check=False, env=env)
    # 扩展必须由超级用户创建：pgvector 的 control 文件没有标 trusted
    run(psql, *base, "-d", DATABASE, "-c", "CREATE EXTENSION IF NOT EXISTS vector;", check=False, env=env)

    check_env = {**os.environ, "PGPASSWORD": PASSWORD}
    result = run(
        psql,
        "-h", "127.0.0.1", "-p", str(port), "-U", ROLE, "-d", DATABASE,
        "-Atc", "select 'pgvector '||extversion from pg_extension where extname='vector';",
        check=False,
        env=check_env,
    )
    print("  校验: " + (result.stdout.strip() or "未安装 pgvector（请检查扩展文件是否放对位置）"))


def dsn(port: int) -> str:
    return f"postgresql+asyncpg://{ROLE}:{PASSWORD}@127.0.0.1:{port}/{DATABASE}"


def main() -> int:
    parser = argparse.ArgumentParser(description="本机 PostgreSQL + pgvector 初始化")
    parser.add_argument("--pg-home", default=str(os.getenv("PG_HOME") or DEFAULT_PG_HOME))
    parser.add_argument("--pgdata", default=str(os.getenv("PGDATA") or DEFAULT_PGDATA))
    parser.add_argument("--pg-version", default=DEFAULT_PG_VERSION)
    parser.add_argument("--port", type=int, default=int(os.getenv("PGPORT") or 5432))
    parser.add_argument("--print-dsn", action="store_true", help="只打印 DSN")
    args = parser.parse_args()

    if args.print_dsn:
        print(f"DATABASE_URL={dsn(args.port)}")
        print("REDIS_URL=redis://127.0.0.1:6379/0")
        return 0

    pg_home, pgdata = Path(args.pg_home), Path(args.pgdata)
    log(f"PG_HOME = {pg_home}")
    log(f"PGDATA  = {pgdata}")
    log(f"PORT    = {args.port}")

    ensure_postgres(pg_home, args.pg_version)
    ensure_pgvector(pg_home, args.pg_version)
    ensure_instance(pg_home, pgdata, args.port)
    ensure_objects(pg_home, args.port)

    log("\n下一步：把下面两行写进 backend/.env，然后执行 python scripts/migrate.py")
    log(f"  DATABASE_URL={dsn(args.port)}")
    log("  REDIS_URL=redis://127.0.0.1:6379/0")
    log("\n验证：python scripts/check_storage.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
