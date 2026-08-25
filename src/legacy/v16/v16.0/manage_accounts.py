#!/usr/bin/env python3
"""Account administration from the command line — the way back in.

The console can do everything this can, except when you cannot sign in to
the console: a forgotten root password, a root account disabled by accident,
or an install where nobody ever created the first account. That is what this
is for. It edits accounts.json directly and needs no running server.

    python manage_accounts.py list
    python manage_accounts.py create alice@innovative-technology.com --level support
    python manage_accounts.py passwd alice@innovative-technology.com
    python manage_accounts.py level alice@innovative-technology.com root
    python manage_accounts.py enable alice@innovative-technology.com
    python manage_accounts.py disable alice@innovative-technology.com
    python manage_accounts.py delete alice@innovative-technology.com

Passwords are prompted for, never passed as arguments — an argument would
land in shell history and in the process list.

Run it from the `src` directory, or with ACCOUNTS_PATH pointing at the file,
so it edits the same store the server reads.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# accounts.py resolves ACCOUNTS_PATH at import, so .env has to be loaded
# before it — same ordering constraint main.py documents at its top.
_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env):
    for _line in open(_env, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import accounts  # noqa: E402


def _ask_password(label="Password") -> str:
    first = getpass.getpass(f"{label}: ")
    if getpass.getpass(f"{label} again: ") != first:
        sys.exit("Those did not match.")
    try:
        accounts.validate_password(first)
    except accounts.AccountError as e:
        sys.exit(str(e))
    return first


def _resolve(email: str) -> dict:
    user = accounts.find_by_email(email)
    if not user:
        sys.exit(f"No account for {email}. `list` shows what exists.")
    return user


def cmd_list(args):
    users = accounts.list_users()
    if not users:
        print("No accounts yet. Create the first one:")
        print("  python manage_accounts.py create you@company.com --level root")
        return
    width = max(len(u["email"]) for u in users)
    print(f"{'EMAIL'.ljust(width)}  {'LEVEL'.ljust(8)}  STATE     LAST SIGN-IN")
    for u in users:
        state = "disabled" if u["disabled"] else "active"
        if u["must_change_password"]:
            state += "*"
        print(f"{u['email'].ljust(width)}  {u['level'].ljust(8)}  "
              f"{state.ljust(9)} {u['last_login'] or 'never'}")
    if any(u["must_change_password"] for u in users):
        print("\n* must change password at next sign-in")
    roots = [u for u in users if u["level"] == "root" and not u["disabled"]]
    if len(roots) == 1:
        print(f"\nOne active root account ({roots[0]['email']}). Consider a "
              f"second, so losing that password does not lock everyone out.")


def cmd_create(args):
    if args.level not in accounts.LEVELS:
        sys.exit(f"level must be one of {', '.join(accounts.LEVELS)}")
    if args.any_domain:
        accounts.ALLOWED_EMAIL_DOMAIN = ""
    password = _ask_password("New password")
    try:
        user = accounts.create_user(
            args.email, password, args.level, name=args.name or "",
            created_by="cli", must_change_password=not args.no_change_required)
    except accounts.AccountError as e:
        sys.exit(str(e))
    print(f"Created {user['email']} as {user['level']}.")
    if user["must_change_password"]:
        print("They will be asked to change this password at first sign-in.")


def cmd_passwd(args):
    user = _resolve(args.email)
    password = _ask_password("New password")
    accounts.set_password(user["id"], password, actor_id="cli",
                          must_change=not args.no_change_required)
    print(f"Password changed for {user['email']}. Any session it had is now "
          f"signed out.")


def cmd_level(args):
    user = _resolve(args.email)
    try:
        out = accounts.set_level(user["id"], args.level, actor_id="cli")
    except accounts.AccountError as e:
        sys.exit(str(e))
    print(f"{out['email']} is now {out['level']}.")


def cmd_disable(args):
    user = _resolve(args.email)
    try:
        accounts.set_disabled(user["id"], True, actor_id="cli")
    except accounts.AccountError as e:
        sys.exit(str(e))
    print(f"Disabled {user['email']}. Its sessions are dead immediately.")


def cmd_enable(args):
    user = _resolve(args.email)
    accounts.set_disabled(user["id"], False, actor_id="cli")
    print(f"Enabled {user['email']}.")


def cmd_delete(args):
    user = _resolve(args.email)
    if input(f"Delete {user['email']} permanently? [y/N] ").strip().lower() != "y":
        sys.exit("Left alone.")
    try:
        accounts.delete_user(user["id"], actor_id="cli")
    except accounts.AccountError as e:
        sys.exit(str(e))
    print(f"Deleted {user['email']}.")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show every account").set_defaults(fn=cmd_list)

    p = sub.add_parser("create", help="add an account")
    p.add_argument("email")
    p.add_argument("--level", default="support", choices=list(accounts.LEVELS))
    p.add_argument("--name", default="")
    p.add_argument("--no-change-required", action="store_true",
                   help="do not force a password change at first sign-in")
    p.add_argument("--any-domain", action="store_true",
                   help="allow an email outside ALLOWED_EMAIL_DOMAIN")
    p.set_defaults(fn=cmd_create)

    p = sub.add_parser("passwd", help="set an account's password")
    p.add_argument("email")
    p.add_argument("--no-change-required", action="store_true")
    p.set_defaults(fn=cmd_passwd)

    p = sub.add_parser("level", help="change an account's level")
    p.add_argument("email")
    p.add_argument("level", choices=list(accounts.LEVELS))
    p.set_defaults(fn=cmd_level)

    for name, fn, helptext in (("enable", cmd_enable, "let an account sign in again"),
                               ("disable", cmd_disable, "block an account"),
                               ("delete", cmd_delete, "remove an account for good")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("email")
        p.set_defaults(fn=fn)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
