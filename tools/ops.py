# coding: utf-8
"""运营工具 CLI

用法(在项目根目录):
    python tools/ops.py init-keys
    python tools/ops.py gen-invite --count 100 --days 14 --balance 100 --out invites.txt
    python tools/ops.py gen-trial --count 50 --days 7  --out trials.txt
    python tools/ops.py gen-recharge --amount 50 --count 30 --out recharges.txt
    python tools/ops.py gen-activation --tier beta --count 50 --days 30
    python tools/ops.py verify --code "INV-XXXX-..."
    python tools/ops.py parse-pricing
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 让脚本可以独立运行(无需依赖包安装)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.utils.signed_code import (  # noqa: E402
    decodeSignedCode,
    makeInviteCode,
    makeRechargeCode,
    makeTrialCode,
    parseSignedModel,
    verifyPayload,
)
from app.core.models.auth_models import InviteCode, RechargeCode, TrialCode  # noqa: E402
from app.core.services.pricing_service import DEFAULT_RULES  # noqa: E402


def cmd_init_keys(_args) -> None:
    """生成 RSA 密钥对占位(本期用 HMAC,保留接口以备 RC+ 切换 RSA-PSS)。"""
    out = ROOT / "tools" / "keys"
    out.mkdir(parents=True, exist_ok=True)
    priv = out / "private.pem"
    pub = out / "public.pem"
    if priv.exists() and pub.exists():
        print(f"[init-keys] 已存在,跳过: {out}")
        return
    priv.write_text(
        "# 当前使用 HMAC-SHA256 共享密钥(LICENSE_SECRET),无独立 RSA 密钥\n"
        "# 此文件作为占位,RC+ 切换 RSA-PSS 时再用 cryptography 生成\n",
        encoding="utf-8",
    )
    pub.write_text(
        "# 公钥占位。RC+ 切换时,客户端内置 public.pem 内容做验签\n",
        encoding="utf-8",
    )
    print(f"[init-keys] 已生成占位密钥: {out}")


def _writeLines(path: Path, lines: list[str], header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        if header:
            f.write(f"# {header}\n# generated_at: {datetime.utcnow().isoformat()}\n\n")
        for ln in lines:
            f.write(ln + "\n")
    print(f"[ok] {len(lines)} 条已写入: {path}")


def cmd_gen_invite(args) -> None:
    lines = []
    for _ in range(args.count):
        lines.append(
            makeInviteCode(
                maxUses=1,
                grantedBalance=args.balance,
                grantedDays=args.days,
                tier=args.tier,
            )
        )
    out = Path(args.out)
    _writeLines(out, lines, header=f"INVITE codes x{args.count}, {args.days}d, +{args.balance}币, tier={args.tier}")


def cmd_gen_trial(args) -> None:
    lines = []
    for _ in range(args.count):
        lines.append(makeTrialCode(grantedBalance=args.balance, grantedDays=args.days))
    out = Path(args.out)
    _writeLines(out, lines, header=f"TRIAL codes x{args.count}, {args.days}d, +{args.balance}币")


def cmd_gen_recharge(args) -> None:
    lines = []
    for _ in range(args.count):
        lines.append(makeRechargeCode(amount=args.amount, note=args.note or ""))
    out = Path(args.out)
    _writeLines(out, lines, header=f"RECHARGE codes x{args.count}, {args.amount}币 each")


def cmd_gen_activation(args) -> None:
    """复用 license.py 的 generateActivationCode 接口(本期走 HMAC)。"""
    from app.core.utils.license import LicenseManager
    from app.core.utils.device_id import generateOrLoadDeviceId

    try:
        deviceCode = generateOrLoadDeviceId()
    except Exception as e:
        print(f"[warn] 无法采集本机设备码,使用占位 deviceCode: {e}")
        deviceCode = "DEVICE-PLACEHOLDER"

    validity = (datetime.utcnow() + timedelta(days=args.days)).strftime("%Y-%m-%d")
    lines = []
    for _ in range(args.count):
        lines.append(
            LicenseManager.generateActivationCode(
                deviceCode=deviceCode,
                validityPeriod=validity,
                userType=args.tier,
            )
        )
    out = Path(args.out)
    _writeLines(out, lines, header=f"ACTIVATION codes x{args.count}, tier={args.tier}, valid until {validity}")


def cmd_verify(args) -> None:
    raw = args.code
    try:
        data = decodeSignedCode(raw)
    except Exception as e:
        print(f"[fail] 解码失败: {e}")
        sys.exit(1)
    signature = data.get("signature")
    payloadWithoutSig = {k: v for k, v in data.items() if k != "signature"}
    if not signature or not verifyPayload(payloadWithoutSig, signature):
        print("[fail] 签名校验失败")
        sys.exit(1)
    print("[ok] 签名校验通过")
    print(json.dumps(payloadWithoutSig, ensure_ascii=False, indent=2))


def cmd_parse_pricing(args) -> None:
    print(json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2))


def cmd_verify_in_database(args) -> None:
    """登记充值码到本地数据库(运营导入本地去重表)。"""
    from app.core.services import account_db

    for code in args.codes:
        try:
            amount, _ = account_db.consumeRechargeCode(code, "OPS")
            print(f"[ok] {code[-12:]} consumed {amount}")
        except Exception as e:
            print(f"[skip] {code[-12:]}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prismatica 运营工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-keys", help="初始化密钥(占位)")

    p = sub.add_parser("gen-invite", help="生成邀请码")
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--balance", type=int, default=100)
    p.add_argument("--tier", default="beta")
    p.add_argument("--out", default="codes/invites.txt")
    p.set_defaults(func=cmd_gen_invite)

    p = sub.add_parser("gen-trial", help="生成体验码")
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--balance", type=int, default=20)
    p.add_argument("--out", default="codes/trials.txt")
    p.set_defaults(func=cmd_gen_trial)

    p = sub.add_parser("gen-recharge", help="生成充值码")
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--amount", type=int, default=50)
    p.add_argument("--note", default="")
    p.add_argument("--out", default="codes/recharges.txt")
    p.set_defaults(func=cmd_gen_recharge)

    p = sub.add_parser("gen-activation", help="生成激活码(走 LicenseManager)")
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--tier", default="beta")
    p.add_argument("--out", default="codes/activations.txt")
    p.set_defaults(func=cmd_gen_activation)

    p = sub.add_parser("verify", help="校验凭证签名")
    p.add_argument("--code", required=True)
    p.set_defaults(func=cmd_verify)

    sub.add_parser("parse-pricing", help="打印默认计价规则 JSON").set_defaults(
        func=cmd_parse_pricing
    )

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()