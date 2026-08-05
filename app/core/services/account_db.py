# coding: utf-8
"""账户 SQLite 持久化

内测期所有账户/账单/充值记录均存于本地 SQLite(<INSTALL_DIR>/datas/account.db)。
RC+ 切换到服务端时,仅需把本模块的 SQL 替换为 HTTP 调用,API 不变。

表设计(与 PRD §7.2 一致):
    - accounts       : 账户基本信息(单行;内测期一机一账户)
    - bills          : 账单流水(pending/settled/refunded/failed)
    - recharge_codes : 已签发充值码的去重表(防本地重复消费)
    - recharge_records: 充值/赠送记录
    - feedback       : 内测反馈(预留 RC 上传)

所有写操作均通过 connection() 上下文管理器自动 commit/rollback,
金额字段均带 CHECK 约束防止出现负余额。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from loguru import logger

from app.core.models.billing_models import (
    Account,
    ActionType,
    BillItem,
    BillStatus,
    RechargeRecord,
)
from app.core.utils.data_paths import DATA_DIR


ACCOUNT_DB: Path = DATA_DIR / "account.db"

# 单文件 SQLite,使用 check_same_thread=False + Lock 串行化写
_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    userId TEXT PRIMARY KEY,
    displayName TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'beta',
    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    frozenBalance INTEGER NOT NULL DEFAULT 0 CHECK (frozenBalance >= 0),
    totalSpent INTEGER NOT NULL DEFAULT 0 CHECK (totalSpent >= 0),
    totalRecharged INTEGER NOT NULL DEFAULT 0 CHECK (totalRecharged >= 0),
    createdAt TEXT NOT NULL,
    updatedAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bills (
    billId TEXT PRIMARY KEY,
    userId TEXT NOT NULL,
    actionType TEXT NOT NULL,
    actionDisplayName TEXT NOT NULL DEFAULT '',
    estimatedCost INTEGER NOT NULL DEFAULT 0,
    realCost INTEGER NOT NULL DEFAULT 0,
    resourceUsed INTEGER NOT NULL DEFAULT 0,
    balanceBefore INTEGER NOT NULL DEFAULT 0,
    balanceAfter INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    taskId TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    createdAt TEXT NOT NULL,
    settledAt TEXT
);

CREATE INDEX IF NOT EXISTS idx_bills_user_status
    ON bills(userId, status, createdAt);

CREATE TABLE IF NOT EXISTS recharge_codes (
    code TEXT PRIMARY KEY,
    amount INTEGER NOT NULL,
    expireAt TEXT NOT NULL,
    usedBy TEXT,
    usedAt TEXT
);

CREATE TABLE IF NOT EXISTS recharge_records (
    recordId TEXT PRIMARY KEY,
    userId TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    source TEXT NOT NULL,
    code TEXT NOT NULL DEFAULT '',
    operatorNote TEXT NOT NULL DEFAULT '',
    balanceBefore INTEGER NOT NULL DEFAULT 0,
    balanceAfter INTEGER NOT NULL DEFAULT 0,
    createdAt TEXT NOT NULL,
    expireAt TEXT
);

CREATE INDEX IF NOT EXISTS idx_recharge_records_user
    ON recharge_records(userId, createdAt);

CREATE TABLE IF NOT EXISTS feedback (
    feedbackId TEXT PRIMARY KEY,
    userId TEXT,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    logPath TEXT,
    createdAt TEXT NOT NULL,
    uploaded INTEGER NOT NULL DEFAULT 0
);
"""


CURRENT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# 连接管理
# ---------------------------------------------------------------------------


def _connect(dbPath: Optional[Path] = None) -> sqlite3.Connection:
    dbPath = dbPath or ACCOUNT_DB
    dbPath.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(dbPath),
        timeout=10.0,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """事务化的连接上下文(自动 commit / rollback)。"""
    conn = _connect()
    try:
        with _LOCK:
            yield conn
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def initSchema() -> None:
    """初始化/升级 schema。幂等。"""
    with connection() as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            ("version", str(CURRENT_SCHEMA_VERSION)),
        )
    logger.info(f"[AccountDB] schema ready v{CURRENT_SCHEMA_VERSION}: {ACCOUNT_DB}")


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------


def upsertAccount(account: Account) -> None:
    """创建或更新账户(单行,内测期一机一账户)。"""
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO accounts (userId, displayName, tier, balance, frozenBalance,
                                  totalSpent, totalRecharged, createdAt, updatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(userId) DO UPDATE SET
                displayName=excluded.displayName,
                tier=excluded.tier,
                updatedAt=excluded.updatedAt
            """,
            (
                account.userId,
                account.displayName,
                account.tier,
                account.balance,
                account.frozenBalance,
                account.totalSpent,
                account.totalRecharged,
                account.createdAt.isoformat(),
                account.updatedAt.isoformat(),
            ),
        )


def getAccount(userId: str) -> Optional[Account]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE userId = ?", (userId,)
        ).fetchone()
    if row is None:
        return None
    return _rowToAccount(row)


def getCurrentAccount() -> Optional[Account]:
    """获取本地唯一账户(内测期假设一机一账户)。"""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM accounts ORDER BY updatedAt DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return _rowToAccount(row)


def _rowToAccount(row: sqlite3.Row) -> Account:
    return Account(
        userId=row["userId"],
        displayName=row["displayName"],
        tier=row["tier"],
        balance=row["balance"],
        frozenBalance=row["frozenBalance"],
        totalSpent=row["totalSpent"],
        totalRecharged=row["totalRecharged"],
        createdAt=datetime.fromisoformat(row["createdAt"]),
        updatedAt=datetime.fromisoformat(row["updatedAt"]),
    )


# ---------------------------------------------------------------------------
# 余额原子操作(单一入口,防止 SQL 散落各处)
# ---------------------------------------------------------------------------


def addBalance(userId: str, delta: int, source: str, code: str = "") -> RechargeRecord:
    """给账户增加余额(充值/赠送/激活赠送)。

    Returns:
        RechargeRecord 实例

    Raises:
        LookupError: 账户不存在
        ValueError:   delta <= 0
    """
    if delta <= 0:
        raise ValueError("delta 必须为正整数")

    now = datetime.utcnow()
    recordId = _genId("rec")

    with connection() as conn:
        row = conn.execute(
            "SELECT balance FROM accounts WHERE userId = ?", (userId,)
        ).fetchone()
        if row is None:
            raise LookupError(f"账户不存在: {userId}")
        balanceBefore = row["balance"]
        balanceAfter = balanceBefore + delta

        conn.execute(
            "UPDATE accounts SET balance = balance + ?, totalRecharged = totalRecharged + ?, updatedAt = ? WHERE userId = ?",
            (delta, delta, now.isoformat(), userId),
        )
        conn.execute(
            """
            INSERT INTO recharge_records (recordId, userId, amount, source, code,
                                          operatorNote, balanceBefore, balanceAfter,
                                          createdAt, expireAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recordId,
                userId,
                delta,
                source,
                code[-4:] if code else "",
                "",
                balanceBefore,
                balanceAfter,
                now.isoformat(),
                None,
            ),
        )

    return RechargeRecord(
        recordId=recordId,
        userId=userId,
        amount=delta,
        source=source,
        code=code[-4:] if code else "",
        balanceBefore=balanceBefore,
        balanceAfter=balanceAfter,
        createdAt=now,
    )


def freezePreauth(userId: str, amount: int) -> int:
    """冻结余额(预占),返回冻结后的 balanceAfter。

    Raises:
        LookupError: 账户不存在
        ValueError:   余额不足或参数错误
    """
    if amount < 0:
        raise ValueError("amount 必须 >= 0")
    with connection() as conn:
        row = conn.execute(
            "SELECT balance FROM accounts WHERE userId = ?", (userId,)
        ).fetchone()
        if row is None:
            raise LookupError(f"账户不存在: {userId}")
        if row["balance"] < amount:
            raise ValueError(f"余额不足: 当前 {row['balance']}, 需要 {amount}")
        conn.execute(
            "UPDATE accounts SET balance = balance - ?, frozenBalance = frozenBalance + ?, updatedAt = ? WHERE userId = ?",
            (amount, amount, datetime.utcnow().isoformat(), userId),
        )
        newBalance = row["balance"] - amount
    return newBalance


def settleFrozen(
    userId: str,
    frozen: int,
    realCost: int,
) -> int:
    """结算:从冻结中扣除实际费用,差额返还。

    Returns:
        结算后的 balance
    """
    if frozen < 0 or realCost < 0:
        raise ValueError("frozen / realCost 必须 >= 0")
    if realCost > frozen:
        # 不应发生(预占 < 实际),自动按 frozen 全扣
        realCost = frozen

    with connection() as conn:
        conn.execute(
            "UPDATE accounts SET frozenBalance = frozenBalance - ?, totalSpent = totalSpent + ?, updatedAt = ? WHERE userId = ?",
            (frozen, realCost, datetime.utcnow().isoformat(), userId),
        )
        if realCost < frozen:
            conn.execute(
                "UPDATE accounts SET balance = balance + ? WHERE userId = ?",
                (frozen - realCost, userId),
            )
        row = conn.execute(
            "SELECT balance FROM accounts WHERE userId = ?", (userId,)
        ).fetchone()
    return row["balance"] if row else 0


def refundFrozen(userId: str, frozen: int) -> int:
    """全额返还冻结(任务失败/取消)。"""
    if frozen < 0:
        raise ValueError("frozen 必须 >= 0")
    with connection() as conn:
        conn.execute(
            "UPDATE accounts SET frozenBalance = frozenBalance - ?, balance = balance + ?, updatedAt = ? WHERE userId = ?",
            (frozen, frozen, datetime.utcnow().isoformat(), userId),
        )
        row = conn.execute(
            "SELECT balance FROM accounts WHERE userId = ?", (userId,)
        ).fetchone()
    return row["balance"] if row else 0


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------


def createBill(
    userId: str,
    actionType: ActionType,
    estimatedCost: int,
    resourceUsed: int,
    balanceBefore: int,
    taskId: str = "",
    description: str = "",
    displayName: str = "",
) -> BillItem:
    """创建 pending 账单。"""
    now = datetime.utcnow()
    billId = _genId("bill")
    item = BillItem(
        billId=billId,
        userId=userId,
        actionType=actionType,
        actionDisplayName=displayName,
        estimatedCost=estimatedCost,
        realCost=estimatedCost,
        resourceUsed=resourceUsed,
        balanceBefore=balanceBefore,
        balanceAfter=balanceBefore,
        status=BillStatus.PENDING,
        taskId=taskId,
        description=description,
        createdAt=now,
    )
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO bills (billId, userId, actionType, actionDisplayName,
                               estimatedCost, realCost, resourceUsed,
                               balanceBefore, balanceAfter, status,
                               taskId, description, createdAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                billId, userId, actionType.value, displayName,
                estimatedCost, estimatedCost, resourceUsed,
                balanceBefore, balanceBefore,
                BillStatus.PENDING.value,
                taskId, description, now.isoformat(),
            ),
        )
    return item


def updateBill(
    billId: str,
    realCost: int,
    resourceUsed: int,
    balanceAfter: int,
    status: BillStatus,
) -> None:
    now = datetime.utcnow()
    with connection() as conn:
        conn.execute(
            """
            UPDATE bills SET realCost = ?, resourceUsed = ?, balanceAfter = ?,
                             status = ?, settledAt = ?
            WHERE billId = ?
            """,
            (realCost, resourceUsed, balanceAfter, status.value, now.isoformat(), billId),
        )


def listBills(
    userId: str,
    status: Optional[BillStatus] = None,
    limit: int = 200,
) -> list[BillItem]:
    with connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM bills WHERE userId = ? AND status = ? ORDER BY createdAt DESC LIMIT ?",
                (userId, status.value, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM bills WHERE userId = ? ORDER BY createdAt DESC LIMIT ?",
                (userId, limit),
            ).fetchall()
    return [_rowToBill(r) for r in rows]


def getBill(billId: str) -> Optional[BillItem]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM bills WHERE billId = ?", (billId,)
        ).fetchone()
    return _rowToBill(row) if row else None


def getPendingBillByTaskId(taskId: str) -> Optional[BillItem]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM bills WHERE taskId = ? AND status = ? ORDER BY createdAt DESC LIMIT 1",
            (taskId, BillStatus.PENDING.value),
        ).fetchone()
    return _rowToBill(row) if row else None


def _rowToBill(row: sqlite3.Row) -> BillItem:
    return BillItem(
        billId=row["billId"],
        userId=row["userId"],
        actionType=ActionType(row["actionType"]),
        actionDisplayName=row["actionDisplayName"],
        estimatedCost=row["estimatedCost"],
        realCost=row["realCost"],
        resourceUsed=row["resourceUsed"],
        balanceBefore=row["balanceBefore"],
        balanceAfter=row["balanceAfter"],
        status=BillStatus(row["status"]),
        taskId=row["taskId"],
        description=row["description"],
        createdAt=datetime.fromisoformat(row["createdAt"]),
        settledAt=datetime.fromisoformat(row["settledAt"]) if row["settledAt"] else None,
    )


# ---------------------------------------------------------------------------
# Recharge Codes(本地去重表)
# ---------------------------------------------------------------------------


def registerRechargeCode(
    code: str, amount: int, expireAt: datetime
) -> None:
    """登记一条充值码(运营签发后写入本地去重表,确保消费一次)。"""
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recharge_codes (code, amount, expireAt) VALUES (?, ?, ?)",
            (code, amount, expireAt.isoformat()),
        )


def consumeRechargeCode(code: str, userId: str) -> tuple[int, datetime]:
    """原子消费充值码(行锁)。

    Returns:
        (amount, expireAt)

    Raises:
        LookupError: 码不存在
        ValueError:   已使用 / 已过期
    """
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM recharge_codes WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            raise LookupError("充值码无效")
        if row["usedBy"]:
            raise ValueError("该充值码已被使用")
        expireAt = datetime.fromisoformat(row["expireAt"])
        if datetime.utcnow() > expireAt:
            raise ValueError("充值码已过期")
        amount = int(row["amount"])

        # 原子更新 usedBy(再次检查防并发)
        cur = conn.execute(
            "UPDATE recharge_codes SET usedBy = ?, usedAt = ? WHERE code = ? AND usedBy IS NULL",
            (userId, datetime.utcnow().isoformat(), code),
        )
        if cur.rowcount == 0:
            raise ValueError("该充值码已被使用")
        return amount, expireAt


def getRechargeCode(code: str) -> Optional[dict]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM recharge_codes WHERE code = ?", (code,)
        ).fetchone()
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Recharge Records
# ---------------------------------------------------------------------------


def listRecharges(userId: str, limit: int = 100) -> list[RechargeRecord]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM recharge_records WHERE userId = ? ORDER BY createdAt DESC LIMIT ?",
            (userId, limit),
        ).fetchall()
    return [
        RechargeRecord(
            recordId=r["recordId"],
            userId=r["userId"],
            amount=r["amount"],
            source=r["source"],
            code=r["code"],
            operatorNote=r["operatorNote"],
            balanceBefore=r["balanceBefore"],
            balanceAfter=r["balanceAfter"],
            createdAt=datetime.fromisoformat(r["createdAt"]),
            expireAt=datetime.fromisoformat(r["expireAt"]) if r["expireAt"] else None,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Feedback(预留)
# ---------------------------------------------------------------------------


def saveFeedback(
    feedbackId: str,
    userId: Optional[str],
    category: str,
    description: str,
    logPath: str = "",
) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO feedback (feedbackId, userId, category, description, logPath, createdAt)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (feedbackId, userId, category, description, logPath, datetime.utcnow().isoformat()),
        )


def listPendingFeedbacks(limit: int = 50) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE uploaded = 0 ORDER BY createdAt DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 一致性校验
# ---------------------------------------------------------------------------


def verifyConsistency(userId: str) -> tuple[bool, str]:
    """启动时调用:校验余额与账单/充值流水是否一致。

    不变量:
        balance == totalRecharged - totalSpent(基于 addBalance/freeze/settle/refund 单调性)
        + frozenBalance >= 0
        + 所有金额 >= 0

    Returns:
        (ok, message)
    """
    acc = getAccount(userId)
    if acc is None:
        return True, "无账户"
    expected = acc.totalRecharged - acc.totalSpent
    if acc.balance != expected:
        return False, (
            f"余额不一致: 当前 balance={acc.balance}, "
            f"totalRecharged={acc.totalRecharged}, "
            f"totalSpent={acc.totalSpent}, expected={expected}"
        )
    if acc.frozenBalance < 0:
        return False, f"frozenBalance 异常: {acc.frozenBalance}"
    return True, "OK"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _genId(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# 模块导入即初始化 schema
initSchema()