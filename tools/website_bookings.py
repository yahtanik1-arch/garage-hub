"""Operator access over SSH, using the server's DB credentials, without Telegram.

python -m tools.website_bookings list
python -m tools.website_bookings confirm 123
python -m tools.website_bookings reject 123 --reason 'Площадка закрыта'
"""
import argparse
import asyncio

from core import db


async def run(args):
    if args.command == "list":
        rows = await db.pending_bookings()
        for b in rows:
            if b.get("web_client_phone"):
                print(b["id"], b["garage_id"], b["start_time"], b["end_time"],
                      b["total_price"], b["web_client_name"], b["web_client_phone"], sep=" | ")
        return
    # Actor 0 denotes an operator authenticated by the OS, not a Telegram user.
    # This helper is not exposed by HTTP and requires the server's service_role key.
    b = await db.booking(args.id)
    if not b or not b.get("web_client_phone"):
        raise SystemExit("Заявка сайта не найдена")
    rows = await db.rpc("gh_booking_decide", p_booking_id=args.id,
        p_status={"confirm": "confirmed", "reject": "rejected", "cancel": "cancelled"}[args.command],
        p_actor_id=0, p_admins=[0], p_reason=args.reason)
    if not rows:
        raise SystemExit("Статус уже изменён; проверьте заявку")
    print("Заявка", args.id, rows[0]["status"])
    print("Сообщите решение клиенту по телефону. Автоматического уведомления нет.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for command in ("confirm", "reject", "cancel"):
        p = sub.add_parser(command)
        p.add_argument("id", type=int)
        p.add_argument("--reason", required=command == "reject", default=None)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
