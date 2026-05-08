# -*- coding: utf-8 -*-
import os
import io
import json
import datetime

from project_paths import get_sync_queue_json_path

TIMEOUT_SECONDS = 180


def _now():
    return datetime.datetime.now()


def _now_str():
    return _now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value):
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _get_fallback_queue_file():
    fallback_dir = os.path.expanduser(r"~\pyRevitSyncLogs")
    if not os.path.exists(fallback_dir):
        os.makedirs(fallback_dir)
    return os.path.join(fallback_dir, "sync_queue.json")


def _get_project_queue_file(doc):
    path = get_sync_queue_json_path(doc)
    if path:
        return path
    return _get_fallback_queue_file()


def get_queue_file(doc):
    return _get_project_queue_file(doc)


def read_queue(doc):
    queue_file = _get_project_queue_file(doc)
    queue_dir = os.path.dirname(queue_file)

    if not os.path.exists(queue_dir):
        os.makedirs(queue_dir)

    if not os.path.exists(queue_file):
        return []

    try:
        with io.open(queue_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def write_queue(doc, queue):
    queue_file = _get_project_queue_file(doc)
    queue_dir = os.path.dirname(queue_file)

    if not os.path.exists(queue_dir):
        os.makedirs(queue_dir)

    with io.open(queue_file, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2)


def _normalize_turn_start(queue):
    if not queue:
        return queue

    first = queue[0]
    if not first.get("turn_start"):
        first["turn_start"] = _now_str()

    for idx in range(1, len(queue)):
        queue[idx]["turn_start"] = None

    return queue


def join_queue(doc, username, model):
    queue = read_queue(doc)

    for item in queue:
        if item.get("username") == username and item.get("model") == model:
            return

    queue.append({
        "username": username,
        "model": model,
        "joined": _now_str(),
        "turn_start": None
    })

    queue = _normalize_turn_start(queue)
    write_queue(doc, queue)


def leave_queue(doc, username, model):
    queue = read_queue(doc)
    new_queue = []

    for item in queue:
        if item.get("username") == username and item.get("model") == model:
            continue
        new_queue.append(item)

    new_queue = _normalize_turn_start(new_queue)
    write_queue(doc, new_queue)


def clear_queue(doc):
    write_queue(doc, [])


def get_queue(doc):
    queue = read_queue(doc)
    queue = _normalize_turn_start(queue)
    write_queue(doc, queue)
    return queue


def get_first_timeout_remaining(doc):
    queue = get_queue(doc)
    if not queue:
        return None

    first = queue[0]
    turn_start = _parse_dt(first.get("turn_start"))
    if not turn_start:
        return TIMEOUT_SECONDS

    elapsed = (_now() - turn_start).seconds
    remaining = TIMEOUT_SECONDS - elapsed
    return remaining if remaining > 0 else 0


def bump_first_if_needed(doc):
    queue = get_queue(doc)
    if not queue:
        return False, queue

    first = queue[0]
    turn_start = _parse_dt(first.get("turn_start"))
    if not turn_start:
        first["turn_start"] = _now_str()
        write_queue(doc, queue)
        return False, queue

    elapsed = (_now() - turn_start).seconds
    if elapsed < TIMEOUT_SECONDS:
        return False, queue

    expired = queue.pop(0)
    expired["turn_start"] = None
    queue.append(expired)

    queue = _normalize_turn_start(queue)
    write_queue(doc, queue)
    return True, queue