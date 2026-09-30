"""
ابزار کنسولی مدیریت اکونومی — مستقیم روی دیتابیس کار می‌کنه.

استفاده:
    python admin_cli.py balance <user_id>
    python admin_cli.py add <user_id> <amount> [reason]
    python admin_cli.py set <user_id> <amount>
    python admin_cli.py history <user_id> [limit]
    python admin_cli.py top [limit]
"""

import asyncio
import sys

from database import Database, InsufficientFunds


async def cmd_balance(db: Database, args: list[str]):
    user_id = int(args[0])
    bal = await db.get_balance(user_id)
    print(f"کاربر {user_id} → موجودی: {bal} سکه")


async def cmd_add(db: Database, args: list[str]):
    user_id = int(args[0])
    amount = int(args[1])
    reason = args[2] if len(args) > 2 else "admin_manual"

    new_balance = await db.add_balance(user_id, amount, reason=reason, allow_negative=True)
    sign = "+" if amount > 0 else ""
    print(f"کاربر {user_id}: {sign}{amount} سکه ثبت شد → موجودی جدید: {new_balance}")


async def cmd_set(db: Database, args: list[str]):
    """موجودی رو مستقیم به یک عدد مشخص تنظیم می‌کنه (نه اضافه/کم، بلکه جایگزین)."""
    user_id = int(args[0])
    target = int(args[1])

    current = await db.get_balance(user_id)
    delta = target - current

    if delta == 0:
        print(f"موجودی کاربر {user_id} از قبل {target} بوده، تغییری لازم نیست.")
        return

    new_balance = await db.add_balance(user_id, delta, reason="admin_set", allow_negative=True)
    print(f"موجودی کاربر {user_id} از {current} به {new_balance} تغییر کرد.")


async def cmd_history(db: Database, args: list[str]):
    user_id = int(args[0])
    limit = int(args[1]) if len(args) > 1 else 10

    rows = await db.get_transaction_history(user_id, limit=limit)
    if not rows:
        print("تراکنشی ثبت نشده.")
        return

    print(f"آخرین {len(rows)} تراکنش کاربر {user_id}:")
    for tx in rows:
        sign = "+" if tx["amount"] > 0 else ""
        print(f"  {sign}{tx['amount']:>8}  ({tx['reason']:<15})  → موجودی: {tx['balance_after']}")


async def cmd_top(db: Database, args: list[str]):
    limit = int(args[0]) if args else 10
    rows = await db.get_leaderboard(limit=limit)
    if not rows:
        print("هنوز کسی موجودی نداره.")
        return

    print(f"🏆 جدول برترین‌های ({len(rows)} نفر):")
    for i, row in enumerate(rows, start=1):
        name = row["username"] or row["first_name"] or "کاربر"
        print(f"  {i}. {name} (id={row['user_id']}) — {row['balance']} سکه")


COMMANDS = {
    "balance": cmd_balance,
    "add": cmd_add,
    "set": cmd_set,
    "history": cmd_history,
    "top": cmd_top,
}


async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return

    cmd_name = sys.argv[1]
    args = sys.argv[2:]

    db = Database()
    await db.connect()
    try:
        await COMMANDS[cmd_name](db, args)
    except InsufficientFunds as e:
        print(f"❌ خطا: {e}")
    except (IndexError, ValueError) as e:
        print(f"❌ آرگومان‌ها نامعتبرن: {e}")
        print(__doc__)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())