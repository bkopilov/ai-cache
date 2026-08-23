#!/usr/bin/env python3
"""Local Chroma vector cache of prior Q&A with repeatable action recipes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COLLECTION = "answers"
DEFAULT_THRESHOLD = 0.55
DEFAULT_N = 5
ANSWER_PREVIEW = 240
PACKAGE = "ai-cache"
CONFIG_FILE = ".ai-cache.json"
ACTION_KEYS = ("type", "summary", "command", "path", "outcome", "notes")
ESCALATE_HINT = (
    "Replay failed — use the AI agent for troubleshooting, then save an updated recipe."
)
_SECRET_RE = re.compile(
    r"(?i)((?:token|password|secret|passwd|authorization|bearer|api[_-]?key)\s*[:=]\s*)\S+"
)
# `{host}` is a flow variable. Skip bash `${HOME}` / `${var}` so those stay as-is.
_PLACEHOLDER_RE = re.compile(r"(?<!\$)\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

_XDG_DEFAULT = Path.home() / ".local" / "share" / "ai-cache"


def _version() -> str:
    try:
        from importlib.metadata import version

        return version(PACKAGE)
    except Exception:
        return "0.1.0"


def _find_project_root() -> tuple[Path | None, dict[str, Any]]:
    """Walk up from cwd looking for .ai-cache.json. Return (root, config)."""
    start = Path.cwd().resolve()
    for path in [start, *start.parents]:
        cfg = path / CONFIG_FILE
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            return path, data if isinstance(data, dict) else {}
    return None, {}


def cache_dir() -> Path:
    env = os.environ.get("AI_CACHE_DIR")
    if env:
        path = Path(env)
    else:
        root, config = _find_project_root()
        if root and config.get("store"):
            store = Path(config["store"])
            path = store if store.is_absolute() else root / store
        elif root:
            path = root / ".ai-cache"
        else:
            path = _XDG_DEFAULT
    path.mkdir(parents=True, exist_ok=True)
    (path / "entries").mkdir(exist_ok=True)
    return path


def entries_dir() -> Path:
    return cache_dir() / "entries"


def collection():
    import chromadb
    from chromadb.utils import embedding_functions

    client = chromadb.PersistentClient(path=str(cache_dir() / "chroma"))
    return client.get_or_create_collection(
        name=COLLECTION,
        embedding_function=embedding_functions.DefaultEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_path(entry_id: str) -> Path:
    return entries_dir() / f"{entry_id}.json"


def _read_entry(entry_id: str) -> dict[str, Any] | None:
    path = _entry_path(entry_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_entry(entry: dict[str, Any]) -> None:
    _entry_path(entry["id"]).write_text(
        json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _score_from_distance(distance: float | None) -> float:
    if distance is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - float(distance)))


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1***", text)


def _placeholders(*texts: str) -> list[str]:
    found: list[str] = []
    for text in texts:
        for match in _PLACEHOLDER_RE.finditer(text or ""):
            key = match.group(1)
            if key not in found:
                found.append(key)
    return found


def _subst(text: str, variables: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return variables[key] if key in variables else match.group(0)

    return _PLACEHOLDER_RE.sub(repl, text)


def _load_variables(args: argparse.Namespace) -> dict[str, str]:
    variables: dict[str, str] = {}
    default_file = cache_dir() / "vars.json"
    paths: list[Path] = []
    if default_file.is_file():
        paths.append(default_file)
    var_file = getattr(args, "var_file", None)
    if var_file:
        paths.append(Path(var_file))
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a JSON object of string values")
        variables.update({str(key): str(value) for key, value in data.items()})
    for item in getattr(args, "var", None) or []:
        if "=" not in item:
            raise ValueError(f"--var must be key=value, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"empty key in --var {item!r}")
        variables[key] = value
    return variables


def _resolve_actions(actions: list[dict[str, Any]], variables: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    for action in actions:
        item = dict(action)
        for field in ("command", "path", "summary", "notes"):
            if field in item and isinstance(item[field], str):
                item[field] = _subst(item[field], variables)
        needed = _placeholders(
            str(item.get("command") or ""),
            str(item.get("path") or ""),
        )
        for key in needed:
            if key not in missing:
                missing.append(key)
        resolved.append(item)
    return resolved, missing


def _load_actions(path: str) -> list[dict[str, Any]]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("actions file must be a JSON array")
    actions: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        action = {key: item[key] for key in ACTION_KEYS if key in item and item[key] not in (None, "")}
        if "command" in action:
            action["command"] = _redact(str(action["command"]))
        if "notes" in action:
            action["notes"] = _redact(str(action["notes"]))[:ANSWER_PREVIEW]
        if action:
            actions.append(action)
    return actions


def _find_duplicate(question: str, threshold: float) -> dict[str, Any] | None:
    """Return the top hit if its score >= threshold, else None."""
    hits = _hits_from_query(question, 1)
    if hits and hits[0]["score"] >= threshold:
        return hits[0]
    return None


def _update_existing_entry(
    entry_id: str,
    question: str,
    answer: str,
    source: str,
    tags: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Overwrite an existing entry's content and re-embed the question."""
    entry = _read_entry(entry_id) or {}
    entry.update(
        {
            "id": entry_id,
            "question": question,
            "answer": answer,
            "source": source,
            "tags": tags,
            "updated_at": _now(),
            "actions": actions,
        }
    )
    coll = collection()
    coll.update(
        ids=[entry_id],
        documents=[question],
        metadatas=[
            {
                "source": source,
                "tags": tags,
                "created_at": entry.get("created_at") or _now(),
                "preview": answer[:ANSWER_PREVIEW],
                "has_actions": bool(actions),
            }
        ],
    )
    _write_entry(entry)
    return entry


def cmd_save(args: argparse.Namespace) -> int:
    question = args.question.strip()
    if not question:
        print("error: question is empty", file=sys.stderr)
        return 2

    if args.answer_file:
        if args.answer_file == "-":
            answer = sys.stdin.read()
        else:
            answer = Path(args.answer_file).read_text(encoding="utf-8")
    else:
        answer = args.answer or ""
    answer = answer.strip()

    actions: list[dict[str, Any]] = []
    if args.actions_file:
        try:
            actions = _load_actions(args.actions_file)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"error: actions file: {exc}", file=sys.stderr)
            return 2

    if not answer and actions:
        answer = f"{len(actions)} cached action(s)"
    if not answer:
        print("error: need --answer/--answer-file or --actions-file", file=sys.stderr)
        return 2

    tags = args.tags or ""
    if actions and "actions" not in {t.strip() for t in tags.split(",") if t.strip()}:
        tags = f"{tags},actions" if tags else "actions"

    force = getattr(args, "force", False)
    quiet = getattr(args, "quiet", False)

    if not force:
        try:
            dup = _find_duplicate(question, DEFAULT_THRESHOLD)
        except Exception:
            dup = None
        if dup:
            entry = _update_existing_entry(dup["id"], question, answer, args.source, tags, actions)
            if not quiet:
                print(json.dumps({
                    "saved": True,
                    "updated": True,
                    "id": dup["id"],
                    "previous_question": dup.get("question", ""),
                    "path": str(_entry_path(dup["id"])),
                }))
            return 0

    entry_id = str(uuid.uuid4())
    entry = {
        "id": entry_id,
        "question": question,
        "answer": answer,
        "source": args.source,
        "tags": tags,
        "created_at": _now(),
        "actions": actions,
    }
    collection().add(
        ids=[entry_id],
        documents=[question],
        metadatas=[
            {
                "source": entry["source"],
                "tags": entry["tags"],
                "created_at": entry["created_at"],
                "preview": answer[:ANSWER_PREVIEW],
                "has_actions": bool(actions),
            }
        ],
    )
    _write_entry(entry)
    if not quiet:
        print(json.dumps({"saved": True, "id": entry_id, "path": str(_entry_path(entry_id))}))
    return 0


def _hits_from_query(question: str, n: int) -> list[dict[str, Any]]:
    coll = collection()
    total = coll.count()
    if total == 0:
        return []
    result = coll.query(
        query_texts=[question],
        n_results=max(1, min(n, total)),
        include=["documents", "metadatas", "distances"],
    )
    ids = (result.get("ids") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    hits: list[dict[str, Any]] = []
    for i, entry_id in enumerate(ids):
        stored = _read_entry(entry_id) or {}
        meta = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
        hits.append(
            {
                "id": entry_id,
                "score": round(_score_from_distance(distances[i] if i < len(distances) else None), 4),
                "question": stored.get("question") or documents[i] if i < len(documents) else "",
                "answer": stored.get("answer") or meta.get("preview") or "",
                "source": stored.get("source") or meta.get("source") or "",
                "tags": stored.get("tags") or meta.get("tags") or "",
                "created_at": stored.get("created_at") or meta.get("created_at") or "",
                "actions": stored.get("actions") or [],
            }
        )
    return hits


def _replay_fail(**fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "replayed": False,
        "escalate": True,
        "hint": ESCALATE_HINT,
    }
    payload.update(fields)
    return payload


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_replay(args: argparse.Namespace) -> int:
    question = args.question.strip()
    if not question:
        print("error: question is empty", file=sys.stderr)
        return 2
    hits = _hits_from_query(question, 1)
    known = bool(hits) and hits[0]["score"] >= args.threshold
    if not known:
        _emit(_replay_fail(reason="not known", query=question, hits=hits))
        return 1
    hit = hits[0]
    actions = hit.get("actions") or []
    if not actions:
        _emit(_replay_fail(reason="hit has no actions — save with --actions-file first", hit=hit))
        return 1

    try:
        variables = _load_variables(args)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: variables: {exc}", file=sys.stderr)
        return 2
    resolved, missing = _resolve_actions(actions, variables)

    if not args.exec:
        _emit(
            {
                "replayed": False,
                "escalate": False,
                "mode": "print",
                "hint": "Same flow, different machine: pass --var host=other-box (or vars.json). Then --exec --yes.",
                "variables": variables,
                "placeholders_needed": _placeholders(
                    *[str(a.get("command") or "") + str(a.get("path") or "") for a in actions]
                ),
                "missing": missing,
                "resolved_actions": resolved,
                "hit": {k: v for k, v in hit.items() if k != "actions"},
                "actions": actions,
            }
        )
        return 0

    if not args.yes:
        print("error: --exec requires --yes (cached shell will run on this machine)", file=sys.stderr)
        return 2
    if missing:
        _emit(
            _replay_fail(
                reason="unresolved placeholders — pass --var key=value (same flow, different machine)",
                missing=missing,
                example=f"--var {missing[0]}=value",
            )
        )
        return 2

    root, _ = _find_project_root()
    cwd = root or Path.cwd()
    ran: list[dict[str, Any]] = []
    for action in resolved:
        command = str(action.get("command") or "").strip()
        kind = str(action.get("type") or "shell")
        step: dict[str, Any] = {
            "summary": action.get("summary") or "",
            "type": kind,
            "command": command or None,
        }
        if kind != "shell" or not command:
            step["skipped"] = True
            step["reason"] = "not a shell command"
            ran.append(step)
            continue
        if "***" in command:
            step["skipped"] = True
            step["reason"] = "command contains redacted secrets; fill in locally and re-save"
            ran.append(step)
            continue
        proc = subprocess.run(command, shell=True, cwd=cwd)
        step["exit"] = proc.returncode
        ran.append(step)
        if proc.returncode != 0:
            _emit(_replay_fail(stopped=True, ran=ran))
            return proc.returncode

    entry = _read_entry(hit["id"])
    if entry:
        entry["last_replayed"] = _now()
        _write_entry(entry)

    _emit({"replayed": True, "escalate": False, "id": hit["id"], "ran": ran})
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    question = args.question.strip()
    if not question:
        print("error: question is empty", file=sys.stderr)
        return 2
    try:
        hits = _hits_from_query(question, args.n)
    except Exception as exc:
        if "not enough elements" in str(exc).lower() or "collection" in str(exc).lower():
            hits = []
        else:
            raise
    known = bool(hits) and hits[0]["score"] >= args.threshold
    payload: dict[str, Any] = {
        "query": question,
        "known": known,
        "threshold": args.threshold,
        "hits": hits,
        "escalate": not known,
    }
    if not known:
        payload["hint"] = "Not in cache — use the AI agent, then save a recipe."
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not args.known_only else (0 if known else 1)


def cmd_list(_args: argparse.Namespace) -> int:
    files = sorted(entries_dir().glob("*.json"))
    entries = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries.append(
            {
                "id": data.get("id", path.stem),
                "question": data.get("question", ""),
                "source": data.get("source", ""),
                "tags": data.get("tags", ""),
                "created_at": data.get("created_at", ""),
            }
        )
    print(json.dumps({"count": len(entries), "entries": entries}, indent=2, ensure_ascii=False))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    entries = []
    for path in sorted(entries_dir().glob("*.json")):
        entries.append(json.loads(path.read_text(encoding="utf-8")))
    payload = {"version": 1, "entries": entries}
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.output and args.output != "-":
        Path(args.output).write_text(text, encoding="utf-8")
        print(json.dumps({"exported": len(entries), "path": args.output}))
    else:
        print(text, end="")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    import tempfile
    from types import SimpleNamespace

    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, dict):
        entries = data.get("entries") or []
    elif isinstance(data, list):
        entries = data
    else:
        print("error: import file must be {entries: [...]} or an array", file=sys.stderr)
        return 2
    imported = 0
    for item in entries:
        if not isinstance(item, dict) or not item.get("question"):
            continue
        actions = item.get("actions") or []
        tmp = None
        try:
            actions_file = None
            if actions:
                tmp = tempfile.NamedTemporaryFile(
                    "w", suffix=".json", delete=False, encoding="utf-8"
                )
                json.dump(actions, tmp)
                tmp.close()
                actions_file = tmp.name
            save_args = SimpleNamespace(
                question=item["question"],
                answer=item.get("answer") or "",
                answer_file=None,
                actions_file=actions_file,
                source=item.get("source") or "import",
                tags=item.get("tags") or "",
                quiet=True,
            )
            rc = cmd_save(save_args)
        finally:
            if tmp is not None:
                Path(tmp.name).unlink(missing_ok=True)
        if rc != 0:
            return rc
        imported += 1
    print(json.dumps({"imported": imported}))
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    entry_id = getattr(args, "id", None)
    question = getattr(args, "question", None)

    if entry_id:
        target_id = entry_id
    elif question:
        hits = _hits_from_query(question.strip(), 1)
        if not hits or hits[0]["score"] < args.threshold:
            print(json.dumps({"deleted": False, "reason": "no matching entry found"}))
            return 1
        target_id = hits[0]["id"]
        if not args.yes:
            print(json.dumps({
                "deleted": False,
                "reason": "found match — pass --yes to confirm deletion",
                "match": {
                    "id": hits[0]["id"],
                    "score": hits[0]["score"],
                    "question": hits[0]["question"],
                },
            }))
            return 1
    elif getattr(args, "all", False):
        if not args.yes:
            count = len(list(entries_dir().glob("*.json")))
            print(json.dumps({
                "deleted": False,
                "reason": f"will delete all {count} entries — pass --yes to confirm",
                "count": count,
            }))
            return 1
        deleted = 0
        for path in list(entries_dir().glob("*.json")):
            path.unlink()
            deleted += 1
        try:
            coll = collection()
            ids = coll.get()["ids"]
            if ids:
                coll.delete(ids=ids)
        except Exception:
            pass
        print(json.dumps({"deleted": True, "count": deleted}))
        return 0
    else:
        print("error: provide --id, --question, or --all", file=sys.stderr)
        return 2

    entry_path = _entry_path(target_id)
    existed = entry_path.is_file()
    if existed:
        entry_path.unlink()

    try:
        collection().delete(ids=[target_id])
    except Exception:
        pass

    print(json.dumps({"deleted": existed, "id": target_id}))
    return 0 if existed else 1


def cmd_stats(_args: argparse.Namespace) -> int:
    files = list(entries_dir().glob("*.json"))
    root, _ = _find_project_root()
    print(
        json.dumps(
            {
                "version": _version(),
                "dir": str(cache_dir()),
                "project_root": str(root) if root else None,
                "entries": len(files),
                "collection": COLLECTION,
            },
            indent=2,
        )
    )
    return 0


def cmd_warmup(_args: argparse.Namespace) -> int:
    from chromadb.utils import embedding_functions

    embedding_functions.DefaultEmbeddingFunction()(["warmup"])
    collection()
    print(json.dumps({"ready": True, "version": _version(), "dir": str(cache_dir())}))
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    root, config = _find_project_root()
    problems: list[str] = []
    try:
        import chromadb  # noqa: F401
    except ImportError:
        problems.append("chromadb is not importable — reinstall ai-cache")
    model = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"
    if not model.exists():
        problems.append("embedding model not downloaded — run: ai-cache warmup")
    if root is None and not os.environ.get("AI_CACHE_DIR"):
        problems.append(
            f"no {CONFIG_FILE} found from cwd and AI_CACHE_DIR is not set — "
            f"create {CONFIG_FILE} in your project root or set AI_CACHE_DIR"
        )
    payload = {
        "ok": not problems,
        "version": _version(),
        "command": str(Path(sys.argv[0]).resolve()),
        "project_root": str(root) if root else None,
        "config": config if config else None,
        "cache_dir": str(cache_dir()),
        "embedding_model": str(model) if model.exists() else None,
        "problems": problems,
    }
    print(json.dumps(payload, indent=2))
    return 0 if not problems else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local vector cache of Q&A. Query before researching; save after a durable answer."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    save = sub.add_parser("save", help="Embed question and store answer on disk")
    save.add_argument("-q", "--question", required=True)
    save.add_argument("-a", "--answer", help="Answer text (use --answer-file for long text)")
    save.add_argument(
        "--answer-file",
        help="Read answer from file, or '-' for stdin",
    )
    save.add_argument("--source", default="chat", help="Origin label (chat, ingest, research, ...)")
    save.add_argument("--tags", default="", help="Comma-separated tags")
    save.add_argument(
        "--actions-file",
        help="JSON array of actions the agent ran (or '-' for stdin). Schema: "
        "[{type, summary, command?, path?, outcome?, notes?}]",
    )
    save.add_argument(
        "--force",
        action="store_true",
        help="Skip dedup check and always create a new entry",
    )
    save.set_defaults(func=cmd_save)

    query = sub.add_parser("query", help="Semantic search; known=true if top hit >= threshold")
    query.add_argument("question")
    query.add_argument("-n", type=int, default=DEFAULT_N, help="Max hits (default 5)")
    query.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Cosine similarity for known=true (default {DEFAULT_THRESHOLD}; MiniLM paraphrases often land 0.55-0.75)",
    )
    query.add_argument(
        "--known-only",
        action="store_true",
        help="Exit 0 if known, 1 otherwise (still prints JSON)",
    )
    query.set_defaults(func=cmd_query)

    replay = sub.add_parser(
        "replay",
        help="Look up a cached action recipe; with --exec --yes run shell steps (no LLM)",
    )
    replay.add_argument("question")
    replay.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Cosine similarity for a hit (default {DEFAULT_THRESHOLD})",
    )
    replay.add_argument(
        "--exec",
        action="store_true",
        help="Execute type=shell commands in order (skips edits/reads)",
    )
    replay.add_argument(
        "--yes",
        action="store_true",
        help="Required with --exec; confirm you want to run the cached shell",
    )
    replay.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Fill a {placeholder} in the flow (repeatable). Example: --var host=lab-2",
    )
    replay.add_argument(
        "--var-file",
        help="JSON object of variables (merged with vars.json in the cache dir if present)",
    )
    replay.set_defaults(func=cmd_replay)

    listing = sub.add_parser("list", help="List stored questions (no vectors)")
    listing.set_defaults(func=cmd_list)

    delete = sub.add_parser("delete", help="Remove a cached entry by ID, question match, or --all")
    delete.add_argument("--id", help="Entry UUID to delete")
    delete.add_argument("-q", "--question", help="Find and delete the best-matching entry")
    delete.add_argument(
        "--all",
        action="store_true",
        help="Delete every entry in the cache",
    )
    delete.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Cosine similarity threshold when deleting by question (default {DEFAULT_THRESHOLD})",
    )
    delete.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion (required for --question and --all)",
    )
    delete.set_defaults(func=cmd_delete)

    stats = sub.add_parser("stats", help="Cache location and entry count")
    stats.set_defaults(func=cmd_stats)

    warmup = sub.add_parser("warmup", help="Download the local embedding model (run once after install)")
    warmup.set_defaults(func=cmd_warmup)

    doctor = sub.add_parser("doctor", help="Check that ai-cache is installed and ready")
    doctor.set_defaults(func=cmd_doctor)

    export = sub.add_parser("export", help="Dump recipes as JSON so another machine can import the same flows")
    export.add_argument("-o", "--output", help="Write to file (default stdout)")
    export.set_defaults(func=cmd_export)

    imported = sub.add_parser("import", help="Load recipes from JSON (re-embeds questions on this machine)")
    imported.add_argument("file", help="Path from ai-cache export, or '-' for stdin")
    imported.set_defaults(func=cmd_import)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
