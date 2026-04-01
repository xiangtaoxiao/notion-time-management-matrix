
### notion_quadrant_manager.py

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import dateparser
import requests

NOTION_VERSION = os.getenv("NOTION_VERSION", "2026-03-11")
NOTION_BASE_URL = "https://api.notion.com/v1"
SG_TZ = ZoneInfo("Asia/Singapore")
STATE_PATH = Path(
    os.getenv(
        "OPENCLAW_NOTION_QM_STATE",
        str(Path.home() / ".openclaw" / "notion_quadrant_manager_state.json"),
    )
)

HIGH_PRIORITY_HINTS = ("高", "重要", "紧急", "high", "urgent", "p1", "p0", "1")
LOW_PRIORITY_HINTS = ("低", "不重要", "普通", "low", "p3", "p4", "2", "3")
DONE_STATUS_HINTS = ("已完成", "完成", "done", "complete", "completed", "finished")
CANCEL_STATUS_HINTS = ("已取消", "取消", "canceled", "cancelled", "aborted", "void")
TODO_STATUS_HINTS = ("未完成", "待办", "todo", "to do", "not started", "进行中", "in progress", "未开始")

FIELD_ALIASES = {
    "title": ["待办事项", "待办", "标题", "task", "name", "title", "事项", "任务"],
    "due": ["截止时间", "截止日期", "due date", "due", "deadline", "日期", "时间", "到期"],
    "priority": ["优先级", "priority", "重要程度", "等级"],
    "status": ["状态", "status", "进度"],
    "note": ["备注", "note", "备注说明", "说明", "描述", "details", "detail"],
    "category": ["分类", "category", "tag", "tags", "类别", "分组"],
}


class NotionQMError(Exception):
    pass


class ConfigError(NotionQMError):
    pass


class SchemaError(NotionQMError):
    pass


class APIError(NotionQMError):
    pass


def sg_now() -> datetime:
    return datetime.now(tz=SG_TZ)


def sg_today() -> date:
    return sg_now().date()


def norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip().lower()


def state_load() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def state_save(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def json_output(ok: bool, action: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
    payload = {"ok": ok, "action": action, "message": message, "data": data or {}}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def make_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_request(
    api_key: str,
    method: str,
    path: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = f"{NOTION_BASE_URL}{path}"
    try:
        resp = requests.request(
            method=method,
            url=url,
            headers=make_headers(api_key),
            json=body,
            params=params,
            timeout=45,
        )
    except requests.RequestException as exc:
        raise APIError(f"Notion 请求失败：{exc}") from exc

    if not resp.ok:
        detail = ""
        try:
            err = resp.json()
            detail = err.get("message") or err.get("error") or resp.text
        except Exception:
            detail = resp.text
        raise APIError(f"Notion API 返回错误 {resp.status_code}：{detail}")

    if resp.text.strip():
        return resp.json()
    return {}


def search_targets(api_key: str, query: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    cursor = None
    while True:
        body: Dict[str, Any] = {"query": query, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        payload = notion_request(api_key, "POST", "/search", body=body)
        results.extend(payload.get("results", []))
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
        if not cursor:
            break
    return results


def get_object_title(obj: Dict[str, Any]) -> str:
    title = obj.get("title")
    if isinstance(title, str):
        return title
    if isinstance(title, list):
        parts = []
        for item in title:
            if isinstance(item, dict):
                parts.append(item.get("plain_text") or item.get("text", {}).get("content", ""))
        return "".join(parts).strip()
    if isinstance(obj.get("name"), str):
        return obj["name"].strip()
    return ""


def match_title_score(candidate: str, query: str) -> int:
    c = norm(candidate)
    q = norm(query)
    if c == q:
        return 100
    score = 0
    if q and q in c:
        score += 60
    if c and c in q:
        score += 20
    for token in re.split(r"\s+", query.strip()):
        token = norm(token)
        if token and token in c:
            score += 5
    return score


def resolve_database(api_key: str, database_name: str) -> Dict[str, Any]:
    cache = state_load()
    cached = cache.get("resolved")
    if cached and norm(cached.get("database_name")) == norm(database_name) and cached.get("data_source_id"):
        return cached

    candidates = []
    for item in search_targets(api_key, database_name):
        obj_type = item.get("object")
        title = get_object_title(item)
        if obj_type in {"database", "data_source"}:
            score = match_title_score(title, database_name)
            if score > 0:
                candidates.append((score, item))

    if not candidates:
        raise ConfigError(f"未找到名称匹配的数据库/数据源：{database_name}")

    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]

    if best.get("object") == "data_source":
        resolved = {
            "database_name": database_name,
            "database_id": best.get("parent", {}).get("database_id") or best.get("database_id"),
            "data_source_id": best["id"],
            "title": get_object_title(best) or database_name,
        }
    else:
        db = notion_request(api_key, "GET", f"/databases/{best['id']}")
        data_sources = db.get("data_sources") or []
        if not data_sources:
            raise SchemaError("数据库已找到，但没有可用的数据源。")
        ds = data_sources[0]
        resolved = {
            "database_name": database_name,
            "database_id": db.get("id") or best["id"],
            "data_source_id": ds.get("id"),
            "title": get_object_title(db) or database_name,
        }

    if not resolved.get("data_source_id"):
        raise SchemaError("未能解析 data_source_id。")

    cache["resolved"] = resolved
    state_save(cache)
    return resolved


def retrieve_schema(api_key: str, resolved: Dict[str, Any]) -> Dict[str, Any]:
    data_source_id = resolved["data_source_id"]
    schema = notion_request(api_key, "GET", f"/data_sources/{data_source_id}")
    properties = schema.get("properties") or {}
    if not properties:
        raise SchemaError("数据库 schema 为空，无法识别字段。")
    return schema


def prop_items(schema: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    props = schema.get("properties") or {}
    items = []
    for key, prop in props.items():
        if isinstance(prop, dict):
            items.append((key, prop))
    return items


def prop_name(prop_key: str, prop: Dict[str, Any]) -> str:
    return prop.get("name") or prop_key or ""


def prop_id(prop_key: str, prop: Dict[str, Any]) -> str:
    return prop.get("id") or prop_key or ""


def prop_type(prop: Dict[str, Any]) -> str:
    return prop.get("type") or ""


def extract_options(prop: Dict[str, Any]) -> List[str]:
    t = prop_type(prop)
    container = prop.get(t) or {}
    options = container.get("options") or []
    names = []
    for opt in options:
        if isinstance(opt, dict) and opt.get("name"):
            names.append(opt["name"])
    return names


def find_property(schema: Dict[str, Any], wanted: str, required_types: Iterable[str]) -> Dict[str, Any]:
    wanted_aliases = [norm(x) for x in FIELD_ALIASES[wanted]]
    matched: List[Tuple[int, str, Dict[str, Any]]] = []
    for key, prop in prop_items(schema):
        name = prop_name(key, prop)
        n = norm(name)
        if prop_type(prop) in set(required_types) and any(alias == n or alias in n or n in alias for alias in wanted_aliases):
            score = 100 if any(alias == n for alias in wanted_aliases) else 50
            matched.append((score, name, prop))
    if not matched:
        if wanted == "title":
            for key, prop in prop_items(schema):
                if prop_type(prop) == "title":
                    return prop
        raise SchemaError(f"缺少必要字段：{wanted}")
    matched.sort(key=lambda x: x[0], reverse=True)
    return matched[0][2]


def build_field_map(schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    fields = {}
    fields["title"] = find_property(schema, "title", ["title"])
    fields["due"] = find_property(schema, "due", ["date"])
    fields["priority"] = find_property(schema, "priority", ["select", "status", "multi_select"])
    fields["status"] = find_property(schema, "status", ["status", "select"])
    fields["note"] = find_property(schema, "note", ["rich_text", "title"])
    fields["category"] = find_property(schema, "category", ["multi_select", "select"])
    return fields


def prop_key_for_page(schema: Dict[str, Any], target_prop: Dict[str, Any]) -> str:
    target_id = prop_id("", target_prop)
    target_name = norm(prop_name("", target_prop))
    for key, prop in prop_items(schema):
        if prop_id(key, prop) == target_id or norm(prop_name(key, prop)) == target_name:
            return key
    return prop_name("", target_prop) or target_id


def option_names(prop: Dict[str, Any]) -> List[str]:
    t = prop_type(prop)
    if t in {"select", "status", "multi_select"}:
        container = prop.get(t) or {}
        opts = container.get("options") or []
        return [o.get("name") for o in opts if isinstance(o, dict) and o.get("name")]
    return []


def choose_option(prop: Dict[str, Any], preferred: Iterable[str], fallback_first: bool = True) -> str:
    opts = option_names(prop)
    if not opts:
        raise SchemaError(f"字段 {prop.get('name', '')} 没有可用枚举值。")
    normalized_opts = [(opt, norm(opt)) for opt in opts]
    for want in preferred:
        nw = norm(want)
        for opt, no in normalized_opts:
            if no == nw or nw in no or no in nw:
                return opt
    if fallback_first:
        return opts[0]
    raise SchemaError(f"无法为字段 {prop.get('name', '')} 选择可用枚举值。")


def priority_level(prop: Dict[str, Any], value: Optional[str]) -> Tuple[str, int]:
    if not value:
        value = choose_option(prop, ["中", "medium", "普通", "normal", "一般"], True)
    opts = option_names(prop)
    if not opts:
        return value, 0
    nv = norm(value)
    for idx, opt in enumerate(opts):
        no = norm(opt)
        if no == nv:
            if any(h in no for h in HIGH_PRIORITY_HINTS):
                return opt, 1
            if any(h in no for h in LOW_PRIORITY_HINTS):
                return opt, 0
            return opt, 2 if idx < max(1, len(opts) // 2) else 0
    for opt in opts:
        no = norm(opt)
        if any(h in no for h in HIGH_PRIORITY_HINTS):
            return opt, 2
    return opts[0], 1 if len(opts) == 1 else 0


def status_value(prop: Dict[str, Any], kind: str) -> str:
    if kind == "done":
        return choose_option(prop, DONE_STATUS_HINTS, True)
    if kind == "cancel":
        return choose_option(prop, CANCEL_STATUS_HINTS, True)
    return choose_option(prop, TODO_STATUS_HINTS, True)


def rich_text_payload(text: str) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": {"content": text}}]


def parse_date_text(text: str) -> Tuple[Optional[date], Optional[str], str]:
    if not text:
        return None, None, ""
    settings = {
        "TIMEZONE": "Asia/Singapore",
        "RETURN_AS_TIMEZONE_AWARE": False,
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": sg_now().replace(tzinfo=None),
    }
    found = dateparser.search.search_dates(text, languages=["zh", "en"], settings=settings)
    if not found:
        dt = dateparser.parse(text, languages=["zh", "en"], settings=settings)
        if not dt:
            return None, None, text.strip()
        return dt.date(), text.strip(), text.strip()
    matched_text, dt = found[0]
    due = dt.date()
    title = text.replace(matched_text, "", 1).strip()
    title = re.sub(r"^[\s,，。．·\-—:：]+", "", title)
    return due, matched_text, title or text.strip()


def page_value(page: Dict[str, Any], prop: Dict[str, Any], key_hint: str) -> Any:
    props = page.get("properties") or {}
    candidates = []
    if key_hint:
        candidates.append(key_hint)
    candidates.append(prop_id(key_hint, prop))
    candidates.append(prop_name(key_hint, prop))
    for key in candidates:
        if key in props:
            return props[key]
    target_id = prop_id(key_hint, prop)
    target_name = norm(prop_name(key_hint, prop))
    for k, v in props.items():
        if norm(k) == target_name or k == target_id:
            return v
        if isinstance(v, dict) and (v.get("id") == target_id or norm(v.get("name")) == target_name):
            return v
    return None


def extract_value(prop: Dict[str, Any], raw: Any) -> Any:
    if raw is None:
        return None
    t = prop_type(prop)
    if t == "title":
        parts = raw.get("title") or []
        return "".join([i.get("plain_text", "") if isinstance(i, dict) else "" for i in parts]).strip()
    if t == "rich_text":
        parts = raw.get("rich_text") or []
        return "".join([i.get("plain_text", "") if isinstance(i, dict) else "" for i in parts]).strip()
    if t == "date":
        d = raw.get("date") or {}
        return d.get("start")
    if t == "select":
        sel = raw.get("select") or {}
        return sel.get("name")
    if t == "status":
        st = raw.get("status") or {}
        return st.get("name")
    if t == "multi_select":
        arr = raw.get("multi_select") or []
        return [i.get("name") for i in arr if isinstance(i, dict) and i.get("name")]
    if t == "checkbox":
        return bool(raw.get("checkbox"))
    return raw


def page_to_task(page: Dict[str, Any], schema: Dict[str, Any], fields: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out = {
        "page_id": page.get("id"),
        "url": page.get("url"),
        "created_time": page.get("created_time"),
        "last_edited_time": page.get("last_edited_time"),
    }
    for k in ("title", "due", "priority", "status", "note", "category"):
        prop = fields[k]
        key_hint = prop_name("", prop)
        raw = page_value(page, prop, key_hint)
        out[k] = extract_value(prop, raw)
    return out


def page_matches_open(task: Dict[str, Any]) -> bool:
    status = str(task.get("status") or "").strip()
    n = norm(status)
    return not any(h in n for h in DONE_STATUS_HINTS) and not any(h in n for h in CANCEL_STATUS_HINTS)


def due_date_value(task: Dict[str, Any]) -> Optional[date]:
    val = task.get("due")
    if not val:
        return None
    try:
        return datetime.strptime(val[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def priority_score(task: Dict[str, Any], fields: Dict[str, Dict[str, Any]]) -> int:
    pr = fields["priority"]
    value = str(task.get("priority") or "")
    opts = option_names(pr)
    if not opts:
        return 0
    nv = norm(value)
    for idx, opt in enumerate(opts):
        no = norm(opt)
        if no == nv:
            if any(h in no for h in HIGH_PRIORITY_HINTS):
                return 2
            if any(h in no for h in LOW_PRIORITY_HINTS):
                return 0
            return 2 if idx < max(1, len(opts) // 2) else 0
    for opt in opts:
        no = norm(opt)
        if any(h in no for h in HIGH_PRIORITY_HINTS):
            return 2
    return 1


def urgent_score(task: Dict[str, Any], urgent_days: int = 2) -> int:
    due = due_date_value(task)
    if not due:
        return 0
    delta = (due - sg_today()).days
    return 1 if delta <= urgent_days else 0


def sort_tasks(tasks: List[Dict[str, Any]], fields: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        tasks,
        key=lambda t: (
            -priority_score(t, fields),
            due_date_value(t) or date.max,
            t.get("created_time") or "",
        ),
    )


def query_data_source(api_key: str, data_source_id: str, filter_obj: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    cursor = None
    while True:
        body: Dict[str, Any] = {"page_size": 100}
        if filter_obj:
            body["filter"] = filter_obj
        if cursor:
            body["start_cursor"] = cursor
        payload = notion_request(api_key, "POST", f"/data_sources/{data_source_id}/query", body=body)
        results.extend(payload.get("results", []))
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
        if not cursor:
            break
    return results


def build_status_filter(fields: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    status_prop = fields["status"]
    done_name = status_value(status_prop, "done")
    cancel_name = status_value(status_prop, "cancel")
    key = prop_name("", status_prop)
    t = prop_type(status_prop)
    if t == "status":
        return {
            "and": [
                {"property": key, "status": {"does_not_equal": done_name}},
                {"property": key, "status": {"does_not_equal": cancel_name}},
            ]
        }
    return {
        "and": [
            {"property": key, "select": {"does_not_equal": done_name}},
            {"property": key, "select": {"does_not_equal": cancel_name}},
        ]
    }


def build_date_filter(fields: Dict[str, Dict[str, Any]], start: date, end: date) -> Dict[str, Any]:
    due_prop = fields["due"]
    key = prop_name("", due_prop)
    return {
        "and": [
            {"property": key, "date": {"on_or_after": start.isoformat()}},
            {"property": key, "date": {"on_or_before": end.isoformat()}},
        ]
    }


def query_open_tasks_in_range(api_key: str, resolved: Dict[str, Any], fields: Dict[str, Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    ds_id = resolved["data_source_id"]
    start = sg_today() - timedelta(days=days)
    end = sg_today() + timedelta(days=days)
    filter_obj = {"and": [build_date_filter(fields, start, end), build_status_filter(fields)]}
    pages = query_data_source(api_key, ds_id, filter_obj)
    tasks = [page_to_task(p, {}, fields) for p in pages]
    tasks = [t for t in tasks if page_matches_open(t)]
    return tasks


def query_today_tasks(api_key: str, resolved: Dict[str, Any], fields: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    ds_id = resolved["data_source_id"]
    due_prop = fields["due"]
    status_filter = build_status_filter(fields)
    key = prop_name("", due_prop)
    filter_obj = {
        "and": [
            {"property": key, "date": {"equals": sg_today().isoformat()}},
            status_filter["and"][0],
            status_filter["and"][1],
        ]
    }
    pages = query_data_source(api_key, ds_id, filter_obj)
    tasks = [page_to_task(p, {}, fields) for p in pages]
    tasks = [t for t in tasks if page_matches_open(t)]
    return tasks


def validate_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    return build_field_map(schema)


def bootstrap(api_key: str, database_name: str) -> Dict[str, Any]:
    resolved = resolve_database(api_key, database_name)
    schema = retrieve_schema(api_key, resolved)
    fields = validate_schema(schema)

    cache = state_load()
    cache["resolved"] = resolved
    cache["fields"] = {
        k: {
            "key": prop_key_for_page(schema, v),
            "id": prop_id("", v),
            "name": prop_name("", v),
            "type": prop_type(v),
            "options": extract_options(v),
        }
        for k, v in fields.items()
    }
    state_save(cache)

    return {
        "resolved": resolved,
        "fields": cache["fields"],
    }


def build_create_properties(
    schema: Dict[str, Any],
    fields: Dict[str, Dict[str, Any]],
    text: str,
    priority: Optional[str],
    note: Optional[str],
    category: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    due, matched_date, title = parse_date_text(text)
    if not due:
        raise SchemaError("无法从任务描述中识别截止时间。")

    title_prop = fields["title"]
    due_prop = fields["due"]
    pr_prop = fields["priority"]
    st_prop = fields["status"]
    note_prop = fields["note"]
    cat_prop = fields["category"]

    chosen_priority, _ = priority_level(pr_prop, priority)
    chosen_status = status_value(st_prop, "todo")

    props: Dict[str, Any] = {
        prop_name("", title_prop): {"title": rich_text_payload(title)},
        prop_name("", due_prop): {"date": {"start": due.isoformat()}},
    }

    if prop_type(pr_prop) in {"select", "status"}:
        props[prop_name("", pr_prop)] = {prop_type(pr_prop): {"name": chosen_priority}}
    elif prop_type(pr_prop) == "multi_select":
        props[prop_name("", pr_prop)] = {"multi_select": [{"name": chosen_priority}]}

    if prop_type(st_prop) == "status":
        props[prop_name("", st_prop)] = {"status": {"name": chosen_status}}
    else:
        props[prop_name("", st_prop)] = {"select": {"name": chosen_status}}

    if note:
        if prop_type(note_prop) == "rich_text":
            props[prop_name("", note_prop)] = {"rich_text": rich_text_payload(note)}
        elif prop_type(note_prop) == "title":
            props[prop_name("", note_prop)] = {"title": rich_text_payload(note)}

    if category:
        if prop_type(cat_prop) == "multi_select":
            props[prop_name("", cat_prop)] = {"multi_select": [{"name": category}]}
        elif prop_type(cat_prop) == "select":
            props[prop_name("", cat_prop)] = {"select": {"name": category}}

    meta = {
        "title": title,
        "due_date": due.isoformat(),
        "priority": chosen_priority,
        "matched_date_text": matched_date,
    }
    return props, meta


def create_task(
    api_key: str,
    database_name: str,
    text: str,
    priority: Optional[str] = None,
    note: Optional[str] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    resolved = resolve_database(api_key, database_name)
    schema = retrieve_schema(api_key, resolved)
    fields = validate_schema(schema)

    props, meta = build_create_properties(schema, fields, text, priority, note, category)
    page = notion_request(
        api_key,
        "POST",
        "/pages",
        body={
            "parent": {"type": "data_source_id", "data_source_id": resolved["data_source_id"]},
            "properties": props,
        },
    )
    task = {
        "page_id": page.get("id"),
        "url": page.get("url"),
        **meta,
    }

    cache = state_load()
    cache["resolved"] = resolved
    cache["fields"] = cache.get("fields") or {}
    cache["last_task"] = task
    state_save(cache)

    return task


def fetch_tasks(api_key: str, database_name: str, mode: str, days: int = 7) -> Dict[str, Any]:
    resolved = resolve_database(api_key, database_name)
    schema = retrieve_schema(api_key, resolved)
    fields = validate_schema(schema)

    if mode == "today":
        pages = query_today_tasks(api_key, resolved, fields)
    else:
        pages = query_open_tasks_in_range(api_key, resolved, fields, days)

    tasks = [page_to_task(p, schema, fields) for p in pages]
    tasks = [t for t in tasks if page_matches_open(t)]
    tasks = sort_tasks(tasks, fields)

    cache = state_load()
    cache["resolved"] = resolved
    cache["fields"] = cache.get("fields") or {}
    if tasks:
        cache["last_task"] = tasks[0]
    state_save(cache)

    return {
        "resolved": resolved,
        "count": len(tasks),
        "tasks": tasks,
    }


def get_last_task_page_id() -> Optional[str]:
    cache = state_load()
    last = cache.get("last_task") or {}
    return last.get("page_id")


def update_task_status(
    api_key: str,
    database_name: str,
    status_kind: str,
    page_id: Optional[str] = None,
    fallback_query_text: Optional[str] = None,
) -> Dict[str, Any]:
    resolved = resolve_database(api_key, database_name)
    schema = retrieve_schema(api_key, resolved)
    fields = validate_schema(schema)

    if not page_id:
        page_id = get_last_task_page_id()
    if not page_id and fallback_query_text:
        recent = fetch_tasks(api_key, database_name, "recent", 30)["tasks"]
        for task in recent:
            if norm(fallback_query_text) in norm(task.get("title")):
                page_id = task["page_id"]
                break
    if not page_id:
        raise NotionQMError("未能定位要更新的任务，请先查询或明确指定任务。")

    status_prop = fields["status"]
    status_name = status_value(status_prop, "done" if status_kind == "done" else "cancel")
    prop = prop_name("", status_prop)
    body = {
        "properties": {
            prop: {
                "status" if prop_type(status_prop) == "status" else "select": {"name": status_name}
            }
        }
    }
    page = notion_request(api_key, "PATCH", f"/pages/{page_id}", body=body)

    cache = state_load()
    last = cache.get("last_task") or {}
    if last.get("page_id") == page_id:
        last["status"] = status_name
        cache["last_task"] = last
    state_save(cache)

    return {
        "page_id": page_id,
        "status": status_name,
        "url": page.get("url"),
    }


def quadrant_of(task: Dict[str, Any], fields: Dict[str, Dict[str, Any]], urgent_days: int = 2) -> str:
    important = priority_score(task, fields) >= 1
    urgent = urgent_score(task, urgent_days=urgent_days) == 1
    if important and urgent:
        return "重要紧急"
    if important and not urgent:
        return "重要不紧急"
    if not important and urgent:
        return "紧急不重要"
    return "不重要不紧急"


def summarize_recent(api_key: str, database_name: str, days: int = 7) -> Dict[str, Any]:
    fetched = fetch_tasks(api_key, database_name, "recent", days)
    resolved = fetched["resolved"]
    schema = retrieve_schema(api_key, resolved)
    fields = validate_schema(schema)
    tasks = fetched["tasks"]

    counts = {"重要紧急": 0, "重要不紧急": 0, "紧急不重要": 0, "不重要不紧急": 0}
    important_urgent: List[Dict[str, Any]] = []
    urgent_unimportant: List[Dict[str, Any]] = []

    for task in tasks:
        q = quadrant_of(task, fields)
        counts[q] += 1
        if q == "重要紧急":
            important_urgent.append(task)
        elif q == "紧急不重要":
            urgent_unimportant.append(task)

    advice = ""
    if tasks:
        first = tasks[0]
        advice = one_sentence_advice(str(first.get("title") or ""))

    return {
        "resolved": resolved,
        "days": days,
        "counts": counts,
        "important_urgent": important_urgent,
        "urgent_unimportant": urgent_unimportant,
        "first_task_advice": advice,
    }


def one_sentence_advice(title: str) -> str:
    t = norm(title)
    if any(k in t for k in ["会议", "开会", "沟通", "电话"]):
        return "先确认时间、参会人和议题，再把要说的内容压成三点。"
    if any(k in t for k in ["报告", "文档", "方案", "总结", "邮件"]):
        return "先列出大纲和结论，再补材料，最后统一润色。"
    if any(k in t for k in ["购买", "下单", "预订", "订", "买"]):
        return "先锁定供应和时间，再确认预算与收货/出行细节。"
    if any(k in t for k in ["整理", "归档", "清理", "搬运"]):
        return "先把任务拆成收集、分类、执行三步，先做最容易开头的一步。"
    return "先把这件事拆成一个能在15分钟内完成的最小下一步。"


def print_tasks_result(result: Dict[str, Any], mode: str) -> None:
    tasks = result["tasks"]
    if not tasks:
        json_output(True, mode, "没有找到符合条件的未完成任务。", result)
        return
    if mode == "today":
        msg = f"找到 {len(tasks)} 条今天未完成任务。"
    else:
        msg = f"找到 {len(tasks)} 条近 {result.get('days', '')} 天内的未完成任务。"
    json_output(True, mode, msg, result)


def main() -> None:
    if len(sys.argv) < 3:
        json_output(False, "unknown", "参数不足。用法：python notion_quadrant_manager.py <action> '<json>'")
        sys.exit(1)

    action = sys.argv[1]
    try:
        args = json.loads(sys.argv[2])
    except Exception as exc:
        json_output(False, action, f"参数 JSON 解析失败：{exc}")
        sys.exit(1)

    api_key = args.get("notion_api_key") or args.get("api_key")
    database_name = args.get("database_name") or args.get("notion_database_name")
    if not api_key:
        json_output(False, action, "缺少 notion_api_key / api_key。")
        sys.exit(1)
    if not database_name:
        json_output(False, action, "缺少 notion_database_name / database_name。")
        sys.exit(1)

    try:
        if action in {"bootstrap", "inspect_schema"}:
            result = bootstrap(api_key, database_name)
            json_output(True, action, "数据库连接与字段识别成功。", result)
            return

        if action == "add":
            text = args.get("text") or args.get("task_text") or ""
            if not text:
                raise ConfigError("add 动作缺少 text。")
            task = create_task(
                api_key=api_key,
                database_name=database_name,
                text=text,
                priority=args.get("priority"),
                note=args.get("note"),
                category=args.get("category"),
            )
            json_output(True, action, "任务创建成功。", task)
            return

        if action == "today":
            result = fetch_tasks(api_key, database_name, "today", 0)
            print_tasks_result(result, "today")
            return

        if action == "recent":
            days = int(args.get("days", 7))
            result = fetch_tasks(api_key, database_name, "recent", days)
            result["days"] = days
            print_tasks_result(result, "recent")
            return

        if action == "complete":
            page_id = args.get("page_id")
            fallback = args.get("text") or args.get("query_text")
            result = update_task_status(api_key, database_name, "done", page_id=page_id, fallback_query_text=fallback)
            json_output(True, action, "任务已标记为已完成。", result)
            return

        if action == "cancel":
            page_id = args.get("page_id")
            fallback = args.get("text") or args.get("query_text")
            result = update_task_status(api_key, database_name, "cancel", page_id=page_id, fallback_query_text=fallback)
            json_output(True, action, "任务已标记为已取消。", result)
            return

        if action == "summary":
            days = int(args.get("days", 7))
            result = summarize_recent(api_key, database_name, days)
            json_output(True, action, "最近任务总结完成。", result)
            return

        raise ConfigError(f"未知 action：{action}")

    except NotionQMError as exc:
        json_output(False, action, str(exc))
        sys.exit(1)
    except Exception as exc:
        json_output(False, action, f"未预期错误：{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()