"""The `brain` CLI. Plain text / JSON output, built for agents to read."""

import argparse
import json
import sys
from pathlib import Path

from brain import db, repo, search


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="brain", description="Second-brain CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply migrations and seed users/spaces from config.toml")

    space = sub.add_parser("space", help="manage spaces").add_subparsers(dest="space_command", required=True)
    space_add = space.add_parser("add")
    space_add.add_argument("name")
    space_add.add_argument("--member", action="append", help="member user (repeatable; default: all users)")
    space.add_parser("list")
    target = space.add_parser("set-target")
    target.add_argument("name")
    target.add_argument("--notion-db", help="Notion database ID")
    target.add_argument("--drive-folder", help="Drive folder ID")

    item = sub.add_parser("item", help="manage items").add_subparsers(dest="item_command", required=True)
    item_add = item.add_parser("add")
    item_add.add_argument("--user", required=True)
    item_add.add_argument("--space", help="default: personal:<user>")
    item_add.add_argument("--title", default="")
    item_add.add_argument("--body", default="", help="body text, or - to read stdin")
    item_add.add_argument("--source", default="")
    item_add.add_argument("--kind", default="note", choices=["note", "link", "screenshot", "draft", "research"])
    item_add.add_argument("--attach", action="append", help="file to attach (repeatable)")
    item_get = item.add_parser("get")
    item_get.add_argument("id", type=int)
    item_get.add_argument("--user", required=True)
    item_list = item.add_parser("list")
    item_list.add_argument("--user", required=True)
    item_list.add_argument("--space")
    item_list.add_argument("--all", action="store_true", help="include archived")
    item_archive = item.add_parser("archive")
    item_archive.add_argument("id", type=int)
    item_archive.add_argument("--user", required=True)

    search_cmd = sub.add_parser("search", help="hybrid FTS + vector search")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--user", required=True)
    search_cmd.add_argument("--limit", type=int, default=10)
    search_cmd.add_argument("--json", action="store_true")

    sub.add_parser("index", help="embed new/changed items")

    sync_cmd = sub.add_parser("sync", help="two-way sync with a remote")
    sync_cmd.add_argument("provider", choices=["notion", "drive"])

    return parser


def _emit(rows: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print("\t".join(str(value) for value in row.values()))


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = db.load_config()
    conn = db.connect()

    if args.command == "migrate":
        ran = db.migrate(conn)
        repo.seed(conn, config)
        print(f"applied {len(ran)} migration(s); users/spaces seeded")

    elif args.command == "space":
        if args.space_command == "add":
            repo.add_space(conn, args.name, args.member)
            print(f"space {args.name} created")
        elif args.space_command == "list":
            _emit([{**s, "members": ",".join(s["members"])} for s in repo.list_spaces(conn)], as_json=False)
        elif args.space_command == "set-target":
            if not args.notion_db and not args.drive_folder:
                raise SystemExit("give --notion-db and/or --drive-folder")
            if args.notion_db:
                repo.set_target(conn, args.name, "notion", args.notion_db)
            if args.drive_folder:
                repo.set_target(conn, args.name, "drive", args.drive_folder)
            print(f"targets set for {args.name}")

    elif args.command == "item":
        if args.item_command == "add":
            body = sys.stdin.read() if args.body == "-" else args.body
            item_id = repo.add_item(conn, args.user, args.space, args.title, body, args.source, args.kind)
            for attach in args.attach or []:
                repo.add_attachment(conn, args.user, item_id, Path(attach), db.brain_dir() / "files")
            print(item_id)
        elif args.item_command == "get":
            row = repo.get_item(conn, args.user, args.id)
            print(json.dumps(dict(row), indent=2))
        elif args.item_command == "list":
            rows = repo.list_items(conn, args.user, args.space, include_archived=args.all)
            _emit([{"id": r["id"], "kind": r["kind"], "title": r["title"], "updated_at": r["updated_at"]} for r in rows], as_json=False)
        elif args.item_command == "archive":
            repo.archive_item(conn, args.user, args.id)
            print(f"archived {args.id}")

    elif args.command == "search":
        embedder = None
        if config.get("embeddings", {}).get("model") and conn.execute("SELECT 1 FROM embeddings LIMIT 1").fetchone():
            embedder = search.litellm_embedder(config)
        results = search.hybrid_search(conn, args.user, args.query, embedder=embedder, limit=args.limit)
        _emit(results, as_json=args.json)

    elif args.command == "index":
        count = search.index_items(conn, search.litellm_embedder(config))
        print(f"embedded {count} item(s)")

    elif args.command == "sync":
        from brain import sync

        if args.provider == "notion":
            from brain.remotes import NotionRemote

            token = config.get("notion", {}).get("token")
            if not token:
                raise SystemExit("no [notion] token in config.toml")
            stats = sync.sync_provider(conn, NotionRemote(token))
        else:
            from brain.remotes import DriveRemote

            token_path = config.get("drive", {}).get("token_path", "drive-token.json")
            stats = sync.sync_provider(conn, DriveRemote(db.brain_dir() / token_path))
        print(json.dumps(stats))


if __name__ == "__main__":
    main()
