"""Crash-safe production run ledger.

The store deliberately records a submission intent (``submitting``) before a
provider is called.  A row left in that state without a remote task id is
ambiguous: callers must reconcile it and must not silently submit it again.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable, Iterator, Mapping


_RUN_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")
_STEP_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")
_TERMINAL_REMOTE_STATES = {"finished", "completed", "succeeded", "failed", "error"}


# 每个 job 提交时记下真实用的来源与模型：适配器 id（如 ark_images / openai_video，本地来源照记）与
# 解析别名之后的模型名。交付包的工具清单优先按这两列，而不是按导出时的设置。
_JOB_SOURCE_COLUMNS = ("provider", "model")


def _add_job_source_columns(conn: sqlite3.Connection) -> None:
    """schema 1 → 2：jobs 表补 provider / model 两列（ALTER TABLE 加列，老行留 NULL）。
    幂等：列已经在就跳过——没标过版本号（user_version=0）的老库走建表分支时也要补。"""

    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for column in _JOB_SOURCE_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT")


# 账本表结构版本：每次改表加一格，并在 MIGRATIONS 里补上一步 {旧版本: callable(conn)}。
# 每一步在同一个写事务里跑（别在步骤里用 executescript，它会先 COMMIT 把事务打断）。
# 只读打开不迁移；可写打开时逐步升级。
# 2：jobs 表加 provider / model（提交时的真实来源与模型）。
SCHEMA_VERSION = 2
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _add_job_source_columns}


class StoreError(RuntimeError):
    """Base class for persistent-state errors."""


class RunCollisionError(StoreError):
    """A stable id was reused with different immutable metadata."""


class AmbiguousSubmissionError(StoreError):
    """A provider may have accepted a request whose task id was not saved."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value: Any, name: str) -> str:
    value = getattr(value, "value", value)
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _json(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RunStore:
    """SQLite/WAL store shared safely by threads and independent processes."""

    def __init__(self, db_path: str | Path, *, readonly: bool = False):
        self.db_path = Path(db_path).expanduser().resolve()
        self.readonly = bool(readonly)
        if self.readonly:
            if not self.db_path.is_file():
                raise StoreError(f"run ledger does not exist: {self.db_path}")
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        connection_target: str | Path = self.db_path
        use_uri = False
        if self.readonly:
            connection_target = self.db_path.as_uri() + "?mode=ro"
            use_uri = True
        self._conn = sqlite3.connect(
            connection_target,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
            uri=use_uri,
        )
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 30000")
            if self.readonly:
                version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
                if version != SCHEMA_VERSION:
                    raise StoreError(
                        f"账本 schema 版本 {version} 与程序需要的 {SCHEMA_VERSION} 不一致（只读打开不会迁移）——用可写方式打开一次即可升级"
                        if 0 < version < SCHEMA_VERSION else f"unsupported store schema: {version}（程序需要 {SCHEMA_VERSION}；新版程序写的账本旧版打不开）"
                    )
            else:
                self._conn.execute("PRAGMA synchronous = FULL")
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._init_schema()
        except BaseException:
            # 打开失败（文件不是库、版本对不上、缺迁移脚本）不能把连接留在手里：Windows 上句柄不放，
            # 文件就删不掉、也覆盖不了——只读探测账本的一方（交付包溯源）每次失败都会漏一个
            self._conn.close()
            raise

    @staticmethod
    def schema_version_of(path: str | Path) -> int:
        conn = sqlite3.connect(str(path))
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise StoreError(f"unsupported store schema: {version}（程序需要 {SCHEMA_VERSION}；新版程序写的账本旧版打不开）")
            if 0 < version < SCHEMA_VERSION:
                self._migrate()
                return
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    stage_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    run_dir TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
                    task_id TEXT,
                    remote_state TEXT,
                    result_url TEXT,
                    asset_id TEXT,
                    artifact_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    submitted_at TEXT,
                    finished_at TEXT,
                    provider TEXT,
                    model TEXT,
                    PRIMARY KEY (run_id, step_id),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS jobs_by_run_status
                    ON jobs(run_id, status);
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_by_remote_task
                    ON jobs(kind, task_id) WHERE task_id IS NOT NULL;
                """
            )
            # 版本号还是 0 的老库（标版本号之前建的）：表已经在，CREATE TABLE IF NOT EXISTS 不会补列，这里补。
            # 补列与标版本号放在同一个写事务里：两个进程同时打开同一本老库，后到的等前者提交后再查列，看到已经有了就跳过
            with self._write() as conn:
                _add_job_source_columns(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _migrate(self) -> None:
        """逐版本升级：每一步是一个 callable(conn)，跑完把 user_version 推进一格。

        整个升级在一个写事务里（BEGIN IMMEDIATE），进了事务再重读版本号：网页与 CLI（或两个 runner）同时可写
        打开同一本老账本时，后到的在 busy_timeout 内等前者提交，重读到已是新版本就直接跳过，不会撞上「列已存在」。
        步骤抛错整体回滚（SQLite 的 ALTER TABLE 是事务性的），账本停在原版本。
        没有回退——旧版程序打不开新账本，这点在打开时就会明说。"""

        with self._write() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            while version < SCHEMA_VERSION:
                step = MIGRATIONS.get(version)
                if step is None:
                    raise StoreError(f"没有从 schema {version} 升到 {version + 1} 的迁移脚本")
                step(conn)
                version += 1
                conn.execute(f"PRAGMA user_version = {version}")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "RunStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        if self.readonly:
            raise StoreError("run ledger is read-only")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @staticmethod
    def _validate_run_id(run_id: str) -> str:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError(
                "run_id must be a 1-128 character slug containing only letters, "
                "digits, dot, underscore, and hyphen"
            )
        return run_id

    @staticmethod
    def _validate_step_id(step_id: str) -> str:
        if not isinstance(step_id, str) or not _STEP_ID.fullmatch(step_id):
            raise ValueError("step_id must be a safe 1-128 character slug")
        return step_id

    @staticmethod
    def _safe_run_dir(run_id: str, run_dir: str | Path) -> Path:
        raw = Path(run_dir).expanduser()
        if ".." in raw.parts:
            raise ValueError("run_dir must not contain '..'")
        resolved = raw.resolve()
        if resolved.name != run_id:
            raise ValueError("run_dir must end with the exact run_id")
        return resolved

    @staticmethod
    def _safe_output(run_dir: str, output_path: str | Path) -> Path:
        path = Path(output_path).expanduser()
        candidate = path if path.is_absolute() else Path(run_dir) / path
        resolved = candidate.resolve()
        root = Path(run_dir).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("output path must stay inside run_dir") from exc
        if resolved == root:
            raise ValueError("output path must name a file inside run_dir")
        return resolved

    @staticmethod
    def _run_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _job_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["ambiguous_submission"] = (
            result["status"] == "submitting" and result["task_id"] is None
        ) or result["status"] == "submission_unknown"
        return result

    def create_run(
        self,
        run_id: str,
        stage_id: str,
        topic_id: str,
        input_hash: str,
        plan_hash: str,
        run_dir: str | Path,
    ) -> dict[str, Any]:
        """Create a run, or replay it only when all immutable fields match."""

        run_id = self._validate_run_id(run_id)
        stage_id = _text(stage_id, "stage_id")
        topic_id = _text(topic_id, "topic_id")
        input_hash = _text(input_hash, "input_hash")
        plan_hash = _text(plan_hash, "plan_hash")
        run_dir_text = str(self._safe_run_dir(run_id, run_dir))
        now = _now()
        immutable = (stage_id, topic_id, input_hash, plan_hash, run_dir_text)
        with self._write() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is not None:
                existing = tuple(row[name] for name in (
                    "stage_id", "topic_id", "input_hash", "plan_hash", "run_dir"
                ))
                if existing != immutable:
                    raise RunCollisionError(f"run_id {run_id!r} already has different inputs")
                return dict(row)
            conn.execute(
                """INSERT INTO runs
                   (run_id, stage_id, topic_id, input_hash, plan_hash, run_dir,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?, ?)""",
                (run_id, *immutable, now, now),
            )
            return dict(conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def amend_input_hash(self, run_id: str, input_hash: str) -> dict[str, Any]:
        """Replace a frozen run's input hash.

        Only the storyvideo runtime calls this, and only after proving the source
        diff is confined to post-production blocks that never reach a paid
        request (see ``StoryVideoRunner.amend_post_layers``).
        """

        run_id = self._validate_run_id(run_id)
        input_hash = _text(input_hash, "input_hash")
        with self._write() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunCollisionError(f"run_id {run_id!r} does not exist")
            conn.execute(
                "UPDATE runs SET input_hash = ?, updated_at = ? WHERE run_id = ?",
                (input_hash, _now(), run_id),
            )
            return dict(conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run_id = self._validate_run_id(run_id)
        with self._lock:
            return self._run_row(
                self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            )

    def delete_pristine_run(self, run_id: str) -> bool:
        """Delete a never-submitted local draft run, and nothing else."""

        if self.readonly:
            raise StoreError("run ledger is read-only")
        run_id = self._validate_run_id(run_id)
        with self._write() as conn:
            run = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return False
            jobs = conn.execute(
                "SELECT * FROM jobs WHERE run_id = ?", (run_id,)
            ).fetchall()
            remote_fields = (
                "task_id",
                "remote_state",
                "result_url",
                "asset_id",
                "artifact_path",
                "submitted_at",
                "finished_at",
            )
            unsafe = (
                run["status"] != "prepared"
                or any(
                    job["status"] != "prepared"
                    or int(job["attempt"] or 0) != 0
                    or any(job[field] is not None for field in remote_fields)
                    for job in jobs
                )
            )
            if unsafe:
                raise StoreError(
                    f"run {run_id!r} is not a pristine never-submitted draft"
                )
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            return True

    def archive_run(self, run_id: str) -> dict[str, Any] | None:
        """把一个 run 的整份记录（run 行 + jobs 行）取出来并从账本里删掉，返回可 JSON 序列化的记录；没有这个 run 返回 None。

        给「归档项目」用：项目文件和产物目录搬进 .webui_deleted 之后，账本里同 id 的行不能原样留下——留下的话
        同 id 重建的项目会被旧 run 挡在 prepare 外面（快照被搬走、hash 对不上），而且报错说的是「你改了文件」。
        记录本身不丢：调用方把返回值写进归档目录的 ledger.json，恢复时用 :meth:`restore_run` 原样放回。
        不像 :meth:`delete_pristine_run` 只肯删没提交过的草稿——这里连花过钱的 run 也搬走，因为花钱的证据跟着
        归档目录走了，账本里只留一行指向已不存在目录的 run 反而是错的状态。
        """

        if self.readonly:
            raise StoreError("run ledger is read-only")
        run_id = self._validate_run_id(run_id)
        with self._write() as conn:
            run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                return None
            jobs = conn.execute("SELECT * FROM jobs WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))   # jobs 靠外键 ON DELETE CASCADE 一起走
            return {
                "schema_version": SCHEMA_VERSION,
                "archived_at": _now(),
                "run": dict(run),
                "jobs": [dict(job) for job in jobs],
            }

    def restore_run(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """把 :meth:`archive_run` 取出来的记录原样放回账本。同 id 已经有 run 时抛 RunCollisionError（不覆盖）。

        只写账本里实际存在的列：归档那会儿的表结构可能比现在旧（少列）或新（多列），少的列按建表默认值，
        多的列丢掉——比整份拒绝恢复要好，run / job 的状态与产物路径才是恢复要保住的东西。
        """

        if self.readonly:
            raise StoreError("run ledger is read-only")
        run = payload.get("run") if isinstance(payload, Mapping) else None
        if not isinstance(run, Mapping) or not run.get("run_id"):
            raise StoreError("ledger archive has no run record")
        run_id = self._validate_run_id(str(run["run_id"]))
        jobs = [job for job in (payload.get("jobs") or ()) if isinstance(job, Mapping)]
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is not None:
                raise RunCollisionError(f"run_id {run_id!r} already exists")
            run_columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()]
            job_columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
            self._insert_row(conn, "runs", {k: v for k, v in run.items() if k in run_columns})
            for job in jobs:
                row = {k: v for k, v in job.items() if k in job_columns}
                row["run_id"] = run_id
                self._insert_row(conn, "jobs", row)
            return dict(conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    @staticmethod
    def _insert_row(conn: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> None:
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [row[column] for column in columns],
        )

    def require_run(self, run_id: str) -> dict[str, Any]:
        result = self.get_run(run_id)
        if result is None:
            raise StoreError(f"unknown run_id: {run_id}")
        return result

    def set_run_status(self, run_id: str, status: str) -> dict[str, Any]:
        run_id = self._validate_run_id(run_id)
        status = _text(status, "status")
        with self._write() as conn:
            changed = conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (status, _now(), run_id),
            ).rowcount
            if not changed:
                raise StoreError(f"unknown run_id: {run_id}")
            return dict(conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def ensure_job(
        self,
        run_id: str,
        step_id: str,
        kind: str,
        request_hash: str,
        output_path: str | Path,
    ) -> dict[str, Any]:
        """Create an idempotent logical job; changed metadata is a collision."""

        run = self.require_run(run_id)
        step_id = self._validate_step_id(step_id)
        kind = _text(kind, "kind")
        request_hash = _text(request_hash, "request_hash")
        output = str(self._safe_output(run["run_dir"], output_path))
        immutable = (kind, request_hash, output)
        now = _now()
        with self._write() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE run_id = ? AND step_id = ?", (run_id, step_id)
            ).fetchone()
            if row is not None:
                existing = tuple(row[name] for name in ("kind", "request_hash", "output_path"))
                if existing != immutable:
                    raise RunCollisionError(
                        f"job {run_id}/{step_id} already has different request metadata"
                    )
                return self._job_row(row)  # type: ignore[return-value]
            conn.execute(
                """INSERT INTO jobs
                   (run_id, step_id, kind, request_hash, output_path, status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?)""",
                (run_id, step_id, *immutable, now, now),
            )
            row = conn.execute(
                "SELECT * FROM jobs WHERE run_id = ? AND step_id = ?", (run_id, step_id)
            ).fetchone()
            return self._job_row(row)  # type: ignore[return-value]

    def amend_job_request_hash(self, run_id: str, step_id: str, request_hash: str) -> dict[str, Any]:
        """Replace one job's request hash (storyvideo post-layer amendments only)."""

        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        request_hash = _text(request_hash, "request_hash")
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            conn.execute(
                "UPDATE jobs SET request_hash = ?, updated_at = ? WHERE run_id = ? AND step_id = ?",
                (request_hash, _now(), run_id, step_id),
            )
            row = conn.execute(
                "SELECT * FROM jobs WHERE run_id = ? AND step_id = ?", (run_id, step_id)
            ).fetchone()
            return self._job_row(row)  # type: ignore[return-value]

    def get_job(self, run_id: str, step_id: str) -> dict[str, Any] | None:
        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        with self._lock:
            return self._job_row(
                self._conn.execute(
                    "SELECT * FROM jobs WHERE run_id = ? AND step_id = ?", (run_id, step_id)
                ).fetchone()
            )

    def attempts_for(
        self, run_id: str, step_id: str, *, request_hash: str | None = None
    ) -> dict[str, Any]:
        """How many times this step has been submitted, and how many with the same request.

        账本没有逐次提交的历史表，但不需要：``ensure_job`` 把 ``request_hash`` 钉死在
        (run_id, step_id) 上（元数据不同就是 ``RunCollisionError``），文字改动永远走
        「修订 → 新版本 → 新 run」，所以一个 run 里同一步的每次 ``attempt`` 都是同一份请求。
        于是 ``same_request == attempt``；调用方可以把当前编译出来的指纹传进来防御性核对，
        对不上（只可能是账本被手工改过）就算 0——宁可少拦，不误拦。已知边界：计数按 run
        （版本）算，修订到新版本后从 0 重新数，即使那一镜的文字没变。
        """

        job = self.get_job(run_id, step_id)
        if job is None:
            return {"total": 0, "same_request": 0, "request_hash": None}
        total = int(job.get("attempt") or 0)
        recorded = job.get("request_hash")
        same = total if request_hash is None or request_hash == recorded else 0
        return {"total": total, "same_request": same, "request_hash": recorded}

    def _require_job(self, conn: sqlite3.Connection, run_id: str, step_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM jobs WHERE run_id = ? AND step_id = ?", (run_id, step_id)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown job: {run_id}/{step_id}")
        return row

    def mark_submitting(
        self,
        run_id: str,
        step_id: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Atomically increment attempt and persist intent before provider.submit().

        ``provider`` / ``model``：这一次提交真实用的适配器 id 与解析后的模型名，随意图一起落库
        （意图先于调用，所以对账时也知道当初打算发给谁）。调用方不知道就传空串，不要猜；不传保持 NULL。
        """

        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        source_provider = str(provider).strip() if provider is not None else None
        source_model = str(model).strip() if model is not None else None
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            if row["status"] == "submitting" and row["task_id"] is None:
                raise AmbiguousSubmissionError(
                    f"{run_id}/{step_id} may already have been submitted; reconcile before retry"
                )
            if row["status"] not in {"prepared", "asset_uploaded", "failed"}:
                raise StoreError(f"cannot submit job in state {row['status']!r}")
            conn.execute(
                """UPDATE jobs SET status = 'submitting', attempt = attempt + 1,
                   task_id = NULL, remote_state = NULL, result_url = NULL,
                   artifact_path = NULL, error = NULL, submitted_at = NULL,
                   finished_at = NULL, provider = ?, model = ?, updated_at = ?
                   WHERE run_id = ? AND step_id = ?""",
                (source_provider, source_model, _now(), run_id, step_id),
            )
            return self._job_row(self._require_job(conn, run_id, step_id))  # type: ignore[return-value]

    def mark_submitted(
        self,
        run_id: str,
        step_id: str,
        task_id: str | None,
        remote_state: str | None = None,
        result_url: str | None = None,
    ) -> dict[str, Any]:
        """Immediately persist the provider task id before any polling."""

        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        remote = _text(remote_state or "pending", "remote_state").lower()
        task = str(task_id).strip() if task_id is not None else None
        task = task or None
        if not task and remote not in _TERMINAL_REMOTE_STATES:
            raise ValueError("task_id may be empty only for a synchronous terminal response")
        status = "finished" if remote in {"finished", "completed", "succeeded"} else (
            "failed" if remote in {"failed", "error"} else "submitted"
        )
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            if row["status"] != "submitting":
                if row["task_id"] == task and row["status"] in {
                    "submitted", "processing", "finished", "succeeded", "failed"
                }:
                    return self._job_row(row)  # type: ignore[return-value]
                raise StoreError(f"cannot record submission in state {row['status']!r}")
            now = _now()
            try:
                conn.execute(
                    """UPDATE jobs SET status = ?, task_id = ?, remote_state = ?,
                       result_url = ?, submitted_at = ?, updated_at = ?,
                       finished_at = CASE WHEN ? IN ('finished', 'failed') THEN ? ELSE NULL END
                       WHERE run_id = ? AND step_id = ?""",
                    (status, task, remote, result_url, now, now, status, now, run_id, step_id),
                )
            except sqlite3.IntegrityError as exc:
                raise RunCollisionError(f"remote task id already belongs to another job: {task}") from exc
            return self._job_row(self._require_job(conn, run_id, step_id))  # type: ignore[return-value]

    def mark_polled(
        self,
        run_id: str,
        step_id: str,
        remote_state: str,
        result_url: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        remote = _text(remote_state, "remote_state").lower()
        status = "processing"
        if remote in {"pending", "queued"}:
            status = "submitted"
        elif remote in {"finished", "completed", "succeeded"}:
            status = "finished"
        elif remote in {"failed", "error"}:
            status = "failed"
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            if not row["task_id"]:
                raise AmbiguousSubmissionError(f"{run_id}/{step_id} has no persisted task_id")
            if row["status"] not in {"submitted", "processing", "finished"}:
                raise StoreError(f"cannot poll job in state {row['status']!r}")
            if row["status"] == "finished" and status != "finished":
                raise StoreError("a finished remote job cannot regress to a non-terminal state")
            now = _now()
            conn.execute(
                """UPDATE jobs SET status = ?, remote_state = ?,
                   result_url = COALESCE(?, result_url), error = ?, updated_at = ?,
                   finished_at = CASE WHEN ? IN ('finished', 'failed') THEN ? ELSE finished_at END
                   WHERE run_id = ? AND step_id = ?""",
                (status, remote, result_url, error, now, status, now, run_id, step_id),
            )
            return self._job_row(self._require_job(conn, run_id, step_id))  # type: ignore[return-value]

    def mark_asset_uploaded(self, run_id: str, step_id: str, asset_id: str) -> dict[str, Any]:
        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        asset_id = _text(asset_id, "asset_id")
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            if row["status"] == "asset_uploaded" and row["asset_id"] == asset_id:
                return self._job_row(row)  # type: ignore[return-value]
            if row["status"] != "prepared":
                raise StoreError(f"cannot record an upload in state {row['status']!r}")
            conn.execute(
                """UPDATE jobs SET asset_id = ?, status = 'asset_uploaded', updated_at = ?
                   WHERE run_id = ? AND step_id = ?""",
                (asset_id, _now(), run_id, step_id),
            )
            return self._job_row(self._require_job(conn, run_id, step_id))  # type: ignore[return-value]

    def mark_succeeded(
        self,
        run_id: str,
        step_id: str,
        result_url: str | None = None,
        artifact_path: str | Path | None = None,
    ) -> dict[str, Any]:
        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            if row["status"] not in {"prepared", "finished", "succeeded"}:
                raise StoreError(
                    f"cannot mark job succeeded from state {row['status']!r}"
                )
            run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            artifact = (
                str(self._safe_output(run["run_dir"], artifact_path)) if artifact_path is not None else None
            )
            now = _now()
            conn.execute(
                """UPDATE jobs SET status = 'succeeded',
                   result_url = COALESCE(?, result_url), artifact_path = ?,
                   error = NULL, finished_at = ?, updated_at = ?
                   WHERE run_id = ? AND step_id = ?""",
                (result_url, artifact, now, now, run_id, step_id),
            )
            return self._job_row(self._require_job(conn, run_id, step_id))  # type: ignore[return-value]

    def mark_failed(self, run_id: str, step_id: str, error: str) -> dict[str, Any]:
        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        error = _text(error, "error")
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            if row["status"] == "succeeded":
                raise StoreError("a succeeded job cannot be changed to failed")
            now = _now()
            conn.execute(
                """UPDATE jobs SET status = 'failed', error = ?, finished_at = ?, updated_at = ?
                   WHERE run_id = ? AND step_id = ?""",
                (error, now, now, run_id, step_id),
            )
            return self._job_row(self._require_job(conn, run_id, step_id))  # type: ignore[return-value]

    def reset_job(
        self,
        run_id: str,
        step_id: str,
        *,
        reason: str,
        preserve_asset: bool = False,
    ) -> dict[str, Any]:
        """Explicitly prepare a terminal job for another paid attempt.

        Normal resume never calls this method.  A caller must first archive the
        current artifact and record the review decision.  The cumulative
        attempt counter is retained and increments on the next submission.
        """

        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        reason = _text(reason, "reason")
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            if row["status"] not in {"succeeded", "failed"}:
                raise StoreError(
                    f"cannot reset job in non-terminal state {row['status']!r}"
                )
            status = "asset_uploaded" if preserve_asset and row["asset_id"] else "prepared"
            conn.execute(
                """UPDATE jobs SET status = ?, task_id = NULL, remote_state = NULL,
                   result_url = NULL, asset_id = CASE WHEN ? THEN asset_id ELSE NULL END,
                   artifact_path = NULL, error = ?, submitted_at = NULL,
                   finished_at = NULL, updated_at = ?
                   WHERE run_id = ? AND step_id = ?""",
                (status, int(bool(preserve_asset)), reason, _now(), run_id, step_id),
            )
            return self._job_row(self._require_job(conn, run_id, step_id))  # type: ignore[return-value]

    def discard_unsubmitted_job(
        self,
        run_id: str,
        step_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Discard prepared/upload-only state without touching a paid attempt.

        This is intentionally narrower than :meth:`reset_job`: a persisted
        submission intent or remote task can never pass this guard.  It is used
        when an upstream retake makes a locally uploaded dependency obsolete
        before generation was submitted.
        """

        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        reason = _text(reason, "reason")
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            if row["status"] not in {"prepared", "asset_uploaded"}:
                raise StoreError(
                    f"cannot discard submitted job in state {row['status']!r}"
                )
            conn.execute(
                """UPDATE jobs SET status = 'prepared', task_id = NULL,
                   remote_state = NULL, result_url = NULL, asset_id = NULL,
                   artifact_path = NULL, error = ?, submitted_at = NULL,
                   finished_at = NULL, updated_at = ?
                   WHERE run_id = ? AND step_id = ?""",
                (reason, _now(), run_id, step_id),
            )
            return self._job_row(self._require_job(conn, run_id, step_id))  # type: ignore[return-value]

    def abandon_ambiguous_submission(
        self,
        run_id: str,
        step_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Reset a submission intent only after an operator proves no task exists.

        The attempt counter is retained, so the next remote task receives a new
        attempt-unique name and can never reconcile to the abandoned intent.
        """

        run_id = self._validate_run_id(run_id)
        step_id = self._validate_step_id(step_id)
        reason = _text(reason, "reason")
        with self._write() as conn:
            row = self._require_job(conn, run_id, step_id)
            if row["status"] != "submitting" or row["task_id"] is not None:
                raise StoreError(
                    f"job is not an ambiguous submission intent: {run_id}/{step_id}"
                )
            status = "asset_uploaded" if row["asset_id"] else "prepared"
            conn.execute(
                """UPDATE jobs SET status = ?, remote_state = NULL,
                   result_url = NULL, artifact_path = NULL, error = ?,
                   submitted_at = NULL, finished_at = NULL, updated_at = ?
                   WHERE run_id = ? AND step_id = ?""",
                (status, reason, _now(), run_id, step_id),
            )
            return self._job_row(self._require_job(conn, run_id, step_id))  # type: ignore[return-value]

    def sources_for(self, run_id: str) -> dict[str, list[dict[str, Any]]]:
        """这个 run 里每类 job 真实提交给了谁：``{kind: [{"provider", "model", "count"}, …]}``（只读，交付包溯源用）。

        只数提交过的 job（``attempt > 0``），按 (provider, model) 合并计数；老账本的老行两列是 NULL，
        带出来是空串——调用方据此知道「有提交但没记来源」，别把它当成没提交过。每个 job 只记最近一次
        提交的来源（重提会覆盖），所以 count 是 job 数不是提交次数。没有这个 run 就返回空字典，不抛。
        """

        run_id = self._validate_run_id(run_id)
        with self._lock:
            rows = self._conn.execute(
                """SELECT kind, COALESCE(provider, '') AS provider, COALESCE(model, '') AS model, COUNT(*) AS count
                   FROM jobs WHERE run_id = ? AND attempt > 0
                   GROUP BY kind, COALESCE(provider, ''), COALESCE(model, '')
                   ORDER BY kind, count DESC, provider, model""",
                (run_id,),
            ).fetchall()
        out: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            out.setdefault(str(row["kind"]), []).append(
                {"provider": str(row["provider"]), "model": str(row["model"]), "count": int(row["count"])}
            )
        return out

    def list_run_ids(self) -> list[str]:
        """Every run id in the ledger, oldest first (startup scans for in-flight work)."""

        with self._lock:
            rows = self._conn.execute("SELECT run_id FROM runs ORDER BY rowid").fetchall()
        return [str(row["run_id"]) for row in rows]

    def snapshot(self, run_id: str) -> dict[str, Any]:
        """Return a JSON-serializable, human-readable run snapshot."""

        run = self.require_run(run_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
        jobs = [self._job_row(row) for row in rows]
        ambiguous = [job["step_id"] for job in jobs if job["ambiguous_submission"]]
        return {
            "schema_version": 1,
            "generated_at": _now(),
            "run": run,
            "jobs": jobs,
            "ambiguous_submissions": ambiguous,
        }


__all__ = [
    "AmbiguousSubmissionError",
    "RunCollisionError",
    "RunStore",
    "StoreError",
]
