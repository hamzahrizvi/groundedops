#!/usr/bin/env python3
"""Mint a widget token for testing (v12.0).

Run from the folder containing quota.py, with the same WIDGET_TOKEN_SECRET
the backend uses:

    python mint_token.py                    -> member token, 8h
    python mint_token.py --tier staff       -> staff token
    python mint_token.py --uid test-2 --hours 1

Produces exactly what the WordPress plugin produces, so a token from here
proves the backend half works before WordPress is involved. If a token from
here is accepted but one from WordPress is not, the fault is in the PHP or
the secret, not in the backend.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", default="test-user-1")
    ap.add_argument("--tier", default="member", choices=["member", "staff"])
    ap.add_argument("--hours", type=int, default=8)
    args = ap.parse_args()

    # Load .env the same way the app does, so the secret matches without
    # having to export it by hand.
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path) and not os.getenv("WIDGET_TOKEN_SECRET"):
        for line in open(env_path):
            line = line.strip()
            if line.startswith("WIDGET_TOKEN_SECRET=") and "=" in line:
                os.environ["WIDGET_TOKEN_SECRET"] = line.split("=", 1)[1].strip()
                break

    if not os.getenv("WIDGET_TOKEN_SECRET"):
        print("ERROR: WIDGET_TOKEN_SECRET is not set (in .env or the environment).")
        print("Generate one with:  python -c \"import secrets;print(secrets.token_hex(32))\"")
        print("Put the SAME value in .env and in WordPress wp-config.php.")
        sys.exit(1)

    import quota
    token = quota.issue_token(args.uid, args.tier, args.hours * 3600)
    print()
    print(f"tier : {args.tier}")
    print(f"uid  : {args.uid}")
    print(f"valid: {args.hours}h")
    print()
    print(token)
    print()


if __name__ == "__main__":
    main()
