#!/usr/bin/env python3
"""Create or update an annotator account for the pulp site.

Run ON THE SERVER (accounts live in ~/shared/khj/.pulp_users.json):
    python3 scripts/add_user.py heejin "Heejin Kim"
    python3 scripts/add_user.py dennis "Dennis Yi Tenen"
It prompts for the password (not shown while typing). Listing accounts:
    python3 scripts/add_user.py --list
Removing one:
    python3 scripts/add_user.py --remove username

Once this file exists, the site's login page offers account login; the
shared passcode keeps working as read-only guest access. Every annotation
is recorded under the account's username.
"""
import getpass
import hashlib
import json
import os
import secrets
import sys

PATH = os.environ.get("PULP_USERS_FILE",
                      os.path.expanduser("~/shared/khj/.pulp_users.json"))


def load():
    try:
        return json.load(open(PATH, encoding="utf-8"))
    except Exception:
        return {}


def save(users):
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=1)
    os.chmod(PATH, 0o600)


def main():
    args = sys.argv[1:]
    users = load()
    if args[:1] == ["--list"]:
        for u, rec in users.items():
            print(f"{u}\t{rec.get('name')}")
        print(f"({len(users)} accounts in {PATH})")
        return
    if args[:1] == ["--remove"] and len(args) == 2:
        if users.pop(args[1], None):
            save(users)
            print(f"removed {args[1]}")
        else:
            print("no such user")
        return
    if len(args) != 2:
        sys.exit("usage: add_user.py <username> \"Display Name\"  "
                 "| --list | --remove <username>")
    username, name = args[0].strip().lower(), args[1].strip()
    if not username.isidentifier():
        sys.exit("username: letters, digits, underscore only")
    pw = getpass.getpass(f"password for {username}: ")
    pw2 = getpass.getpass("again: ")
    if pw != pw2 or len(pw) < 4:
        sys.exit("passwords differ or too short (min 4)")
    salt = secrets.token_hex(8)
    users[username] = {
        "name": name, "salt": salt,
        "pw": hashlib.sha256((salt + pw).encode()).hexdigest(),
    }
    save(users)
    print(f"saved {username} ({name}) to {PATH}")


if __name__ == "__main__":
    main()
