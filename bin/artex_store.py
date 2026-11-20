#!/usr/bin/env python3
"""OpenCode -> ARTEX engagement store. Raw JSONL is authoritative; DB mirrors feed the UI."""

import hashlib, json, os, subprocess, sys, datetime

BASE = "/root/ARTEX"
ENG = os.path.join(BASE, "data", "engagements")
PTR = os.path.join(BASE, "data", ".current_engagement")
PSQL = [
    "docker",
    "compose",
    "-f",
    os.path.join(BASE, "docker-compose.yml"),
    "exec",
    "-T",
    "postgres",
    "psql",
    "-U",
    "artex",
    "-d",
    "artex",
    "-v",
    "ON_ERROR_STOP=1",
]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sql(statement):
    r = subprocess.run(
        PSQL + ["-t", "-A", "-c", statement], cwd=BASE, capture_output=True, text=True
    )
    if r.returncode:
        print("PSQL ERROR:", r.stderr.strip(), file=sys.stderr)
        return None
    return r.stdout.strip()


def q(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("\0", "").replace("'", "''") + "'"


def j(v):
    return q(json.dumps(v if v is not None else {}, ensure_ascii=False))


def one(statement):
    out = sql(statement)
    return out.splitlines()[0].strip() if out else None


def slug(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")


def ensure_exploration(goal):
    """ARTEX requires exploration_id on tasks/activity; reuse or create one."""
    eid = one("SELECT id FROM explorations ORDER BY id LIMIT 1")
    if not eid:
        sql(
            "INSERT INTO explorations(goal,status,created_at,updated_at,round_no) "
            "VALUES(%s,'open',now(),now(),0)" % q(goal or "OpenCode engagement")
        )
        eid = one("SELECT id FROM explorations ORDER BY id DESC LIMIT 1")
    return eid


def current_eng():
    try:
        e = open(PTR).read().strip()
        if e:
            return e
    except OSError:
        pass
    return "live-" + now()[:10]


def cmd_engage(args):
    eng = args[0]
    os.makedirs(os.path.join(ENG, eng), exist_ok=True)
    with open(PTR, "w") as f:
        f.write(eng)
    print("engagement pointer:", eng)


def cmd_init(args):
    r = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            os.path.join(BASE, "docker-compose.yml"),
            "up",
            "-d",
        ],
        cwd=BASE,
        capture_output=True,
        text=True,
        timeout=180,
    )
    print(r.stdout.strip() or r.stderr.strip())
    os.makedirs(ENG, exist_ok=True)
    print("ARTEX up; engagement root:", ENG)


def cmd_company(args):
    name = args[0]
    nkey = slug(name)
    cid = one("SELECT id FROM companies WHERE nkey=%s LIMIT 1" % q(nkey))
    if not cid:
        sql(
            "INSERT INTO companies(name,nkey,created_at,updated_at) VALUES(%s,%s,now(),now())"
            % (q(name), q(nkey))
        )
        cid = one("SELECT id FROM companies WHERE nkey=%s LIMIT 1" % q(nkey))
    print(cid)


def cmd_scope(args):
    cid, kind, value = args[0], args[1], args[2]
    reason = args[3] if len(args) > 3 else "opencode-engagement"
    if kind == "domain":
        col, val = "domain", q(value)
    elif kind in ("ip", "cidr"):
        col, val = "net", q(value) + "::cidr"
    else:
        kind, col, val = "icp", "value", q(value)
    sql(
        "INSERT INTO company_scope(company_id,kind,%s,raw,reason,created_at) VALUES(%s,%s,%s,%s,%s,now()) "
        "ON CONFLICT DO NOTHING" % (col, q(cid), q(kind), val, q(value), q(reason))
    )
    print("scope recorded")


def cmd_asset(args):
    cid, typ, value = args[0], args[1], args[2]
    extra = json.loads(args[3]) if len(args) > 3 else {}
    dom = value if ("." in value and not value.replace(".", "").isdigit()) else None
    ip = value if (dom is None) else None
    key = (
        "company_id=%s AND type=%s AND domain IS NOT DISTINCT FROM %s AND ip IS NOT DISTINCT FROM %s"
        % (q(cid), q(typ), q(dom), q(ip))
    )
    aid = one("SELECT id FROM assets WHERE %s LIMIT 1" % key)
    if aid:
        sql(
            "UPDATE assets SET last_seen=now(), updated_at=now(), extra=%s WHERE id=%s"
            % (j(extra), q(aid))
        )
    else:
        sql(
            "INSERT INTO assets(type,company_id,company_source,domain,root_domain,ip,extra,created_at,last_seen,updated_at) "
            "VALUES(%s,%s,'explicit',%s,%s,%s,%s,now(),now(),now())"
            % (q(typ), q(cid), q(dom), q(dom), q(ip), j(extra))
        )
        aid = one("SELECT id FROM assets WHERE %s LIMIT 1" % key)
    print(aid)


def cmd_task(args):
    cid, goal = args[0], args[1]
    name = args[2] if len(args) > 2 else ("Engagement " + now()[:10])
    # tasks.exploration_id is UNIQUE -> every task needs its own exploration.
    sql(
        "INSERT INTO explorations(goal,status,created_at,updated_at,round_no) "
        "VALUES(%s,'open',now(),now(),0)" % q(goal)
    )
    eid = one("SELECT id FROM explorations ORDER BY id DESC LIMIT 1")
    sql(
        "INSERT INTO tasks(name,description,goal,exploration_id,company_id,status,created_at,updated_at) "
        "VALUES(%s,%s,%s,%s,%s,'running',now(),now())"
        % (q(name), q(goal), q(goal), q(eid), q(cid))
    )
    print(
        one(
            "SELECT id FROM tasks WHERE company_id=%s AND goal=%s ORDER BY id DESC LIMIT 1"
            % (q(cid), q(goal))
        )
    )


def cmd_finding(args):
    tid, name, severity, vulnclass, summary, evidence = args[:6]
    worker = args[6] if len(args) > 6 else "opencode"
    asset_ids = json.loads(args[7]) if len(args) > 7 else []
    sql(
        "INSERT INTO findings(task_id,name,severity,vulnclass,summary,evidence,worker,asset_ids,status,created_at,evidence_version,report_evidence_version) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'open',now(),1,1)"
        % (
            q(tid),
            q(name),
            q(severity),
            q(vulnclass),
            q(summary),
            q(evidence),
            q(worker),
            j(asset_ids),
        )
    )
    print(
        one(
            "SELECT id FROM findings WHERE task_id=%s AND name=%s ORDER BY id DESC LIMIT 1"
            % (q(tid), q(name))
        )
    )


def cmd_act(args):
    tid, kind, summary = args[:3]
    detail = args[3] if len(args) > 3 else ""
    meta = json.loads(args[4]) if len(args) > 4 else {}
    meta["opencode_task_id"] = tid
    eid = (
        one("SELECT exploration_id FROM tasks WHERE id=%s" % q(tid))
        if tid not in ("NULL", "None", "")
        else None
    ) or ensure_exploration("OpenCode activity")
    sql(
        "INSERT INTO activity(exploration_id,worker,kind,summary,detail,metadata,created_at) "
        "VALUES(%s,'opencode',%s,%s,%s,%s,now())"
        % (q(eid), q(kind), q(summary), q(detail), j(meta))
    )
    print("activity recorded")


def cmd_tool(args):
    tool, agent = args[0], args[1]
    tid = args[2] if len(args) > 2 else "NULL"
    sess = args[3] if len(args) > 3 else "NULL"
    eid = (
        one("SELECT exploration_id FROM tasks WHERE id=%s" % q(tid))
        if tid != "NULL"
        else None
    ) or ensure_exploration("OpenCode tool call")
    sql(
        "INSERT INTO tool_usage(ts,tool_key,agent_key,task_id,exploration_id,session_id) "
        "VALUES(now(),%s,%s,%s,%s,%s)" % (q(tool), q(agent), q(tid), q(eid), q(sess))
    )
    print("tool call recorded")


def record(args):
    eng = args[0] if args else current_eng()
    data = json.loads(sys.stdin.read())
    d = os.path.join(ENG, eng)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "events.jsonl")
    seq = 1
    prev = "0" * 64
    if os.path.exists(path):
        with open(path, "rb") as f:
            for line in f:
                if line.strip():
                    try:
                        o = json.loads(line)
                        seq = o.get("seq", 0) + 1
                        prev = o.get("sha256", prev)
                    except Exception:
                        pass
    rec = {"seq": seq, "ts": now(), "prev": prev, "data": data}
    rec["sha256"] = hashlib.sha256(
        json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    with open(path, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    typ = data.get("type", "")
    if typ == "finding" and data.get("task_id"):
        cmd_finding(
            [
                str(data["task_id"]),
                data.get("name", "finding"),
                data.get("severity", "medium"),
                data.get("vulnclass", "logic"),
                data.get("summary", ""),
                data.get("evidence", ""),
                data.get("worker", "opencode"),
                json.dumps(data.get("asset_ids", [])),
            ]
        )
    elif typ in ("signal", "traffic", "tool_call", "note", "state"):
        cmd_act(
            [
                str(data.get("task_id", "NULL")),
                typ,
                data.get("summary", "")[:4000],
                json.dumps(data.get("data", data), ensure_ascii=False)[:16000],
                json.dumps({"sha256": rec["sha256"], "seq": seq, "ts": rec["ts"]}),
            ]
        )
    print(json.dumps({"seq": seq, "sha256": rec["sha256"], "file": path}))


def cmd_read(args):
    eng = args[0]
    d = os.path.join(ENG, eng)
    path = os.path.join(d, "events.jsonl")
    if os.path.exists(path):
        print("=== RAW EVENTS (authoritative) ===")
        print(open(path).read(), end="")
    print("=== DB MIRROR ===")
    for table, col in (
        ("companies", "name"),
        ("assets", "id"),
        ("tasks", "goal"),
        ("findings", "name"),
        ("activity", "summary"),
        ("tool_usage", "tool_key"),
        ("company_scope", "raw"),
    ):
        out = sql("SELECT id, %s FROM %s ORDER BY id DESC LIMIT 20" % (col, table))
        print("-- %s --\n%s" % (table, out or "(none)"))


CMDS = {
    "init": cmd_init,
    "engage": cmd_engage,
    "company": cmd_company,
    "scope": cmd_scope,
    "asset": cmd_asset,
    "task": cmd_task,
    "finding": cmd_finding,
    "act": cmd_act,
    "tool": cmd_tool,
    "read": cmd_read,
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "usage: artex_store.py <init|company|scope|asset|task|finding|act|tool|read|record> [args...]"
        )
        sys.exit(2)
    c = sys.argv[1]
    if c == "record":
        record(sys.argv[2:])
    elif c in CMDS:
        CMDS[c](sys.argv[2:])
    else:
        print("unknown command:", c, file=sys.stderr)
        sys.exit(2)
