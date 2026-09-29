import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import httpx
from pymongo import MongoClient
from temporalio.client import Client, WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError
from gax.config import get_settings
from gax.credentials import build_broker
from gax.credentials.local_only_broker import GRANTS_COLLECTION, MongoGrantStore
from gax.models import Incident
from gax.remediation.activities import AUDIT, INCIDENTS, LEDGER
from gax.remediation.types import ApprovalInput, RemediationInput
from gax.retrieval import search
from gax.retrieval.ingest import IngestInput
from gax.retrieval.voyage import VoyageClient
from gax.workflows import INCIDENT_ID_REUSE, TASK_QUEUE, IngestWorkflow, RemediationWorkflow

ROOT = Path(__file__).resolve().parents[2]


def out(value) -> None:
    print(json.dumps(value, indent=2, default=str))


def db(s):
    return MongoClient(s.mongo_uri, serverSelectionTimeoutMS=5000)[s.gax_db]


def broker(s):
    return build_broker(s, store=MongoGrantStore(db(s)[GRANTS_COLLECTION]))


async def temporal(s) -> Client:
    return await Client.connect(s.temporal_address, data_converter=pydantic_data_converter)


async def cmd_ingest(s, a):
    client = await temporal(s)
    wid = f"ingest-{a.version}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    print(f"started {wid}", flush=True)
    report = await client.execute_workflow(IngestWorkflow.run, IngestInput(corpus_dir=str(Path(a.corpus).resolve()), version=a.version),
                                           id=wid, task_queue=TASK_QUEUE)
    out(report.model_dump())


async def cmd_search(s, a):
    voyage = VoyageClient(s.voyage_base_url, s.voyage_api_key)
    hits = search(db(s), voyage, a.query, k=a.k, rerank=not a.no_rerank)
    for i, h in enumerate(hits, 1):
        rr = f" rerank={h.rerank_score:.4f}" if h.rerank_score is not None else ""
        print(f"{i}. {h.chunk_id} vector={h.vector_score:.4f}{rr}\n   {h.text.splitlines()[1][:140]}")


async def cmd_incident_start(s, a):
    incident = Incident(incident_id=a.id, environment=a.env, target=a.target, summary=a.summary)
    injected = Path(a.proposal_file).read_text(encoding="utf-8") if a.proposal_file else None
    inp = RemediationInput(incident=incident, proposal_json=injected, rerank=not a.no_rerank, retrieve=not a.no_retrieval,
                           approval_timeout_seconds=a.approval_timeout)
    client = await temporal(s)
    try:
        handle = await client.start_workflow(RemediationWorkflow.run, inp, id=a.id, task_queue=TASK_QUEUE, id_reuse_policy=INCIDENT_ID_REUSE)
    except WorkflowAlreadyStartedError:
        print(f"rejected: incident {a.id} is running or already completed")
        return 2
    print(f"started {handle.id} run_id={handle.result_run_id} proposal_source={'file' if injected else 'llm'}", flush=True)
    if a.wait:
        return await wait_result(handle)


async def wait_result(handle) -> int:
    try:
        result = await handle.result()
        print(f"final status {result.status}")
        return 0
    except WorkflowFailureError as e:
        print(f"final status {getattr(e.cause, 'type', None)} ({e.cause})")
        return 1


async def cmd_decide(s, a):
    handle = (await temporal(s)).get_workflow_handle(a.id)
    update = RemediationWorkflow.approve if a.command == "approve" else RemediationWorkflow.reject
    try:
        print(await handle.execute_update(update, ApprovalInput(by=a.by, comment=a.comment)))
    except (WorkflowUpdateFailedError, RPCError) as e:
        print(f"rejected: {getattr(e, 'cause', None) or e}")
        return 2
    if a.wait:
        return await wait_result(handle)


async def cmd_status(s, a):
    handle = (await temporal(s)).get_workflow_handle(a.id)
    desc = await handle.describe()
    out({"workflow_id": a.id, "run_id": desc.run_id, "execution_status": desc.status.name if desc.status else None,
         **await handle.query(RemediationWorkflow.current_status)})


async def cmd_audit(s, a):
    d = db(s)
    out({"incident": d[INCIDENTS].find_one({"_id": a.id}),
         "audit_events": list(d[AUDIT].find({"workflow_id": a.id}).sort("at", 1)),
         "action_ledger": list(d[LEDGER].find({"workflow_id": a.id}, {"_id": 0}).sort("at", 1))})


async def cmd_broker(s, a):
    b = broker(s)
    if a.op == "revoke":
        b.revoke(a.action)
    elif a.op == "restore":
        b.restore(a.action)
    else:
        b.set_transient(a.action, a.count)
    out({"mode": b.mode, "grants": list(db(s)[GRANTS_COLLECTION].find())})


async def cmd_fleet(s, a):
    with httpx.Client(base_url=s.fleet_api_url, timeout=10) as fleet:
        if a.op == "faults":
            if a.clear:
                fleet.delete("/admin/faults")
            elif a.fail_next or a.latency_ms or a.drop_next:
                fleet.post("/admin/faults", json={"action": a.action, "fail_next": a.fail_next, "latency_ms": a.latency_ms,
                                                  "drop_next": a.drop_next}).raise_for_status()
            out(fleet.get("/admin/faults").json())
        elif a.op == "counters":
            if a.clear:
                fleet.delete("/admin/counters")
            out(fleet.get("/admin/counters").json())
        elif a.op == "reset":
            out(fleet.post("/admin/reset").json())
        else:
            token = broker(s).issue("get_state", a.target, a.env, "gax-cli", 1).token
            r = fleet.get(f"/v1/{a.env}/consumers/{a.target}", headers={"Authorization": f"Bearer {token}"})
            out(r.json())


async def cmd_demo(s, a):
    from gax.demos import DEMOS
    if a.op == "list":
        for name, module in DEMOS.items():
            print(f"{name:22} {module.TITLE}")
        return 0
    from gax.demos.runner import run
    return await run(a.name, s)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gax")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("ingest")
    c.add_argument("--corpus", default=str(ROOT / "corpus" / "runbooks"))
    c.add_argument("--version", default="v1")
    c.set_defaults(fn=cmd_ingest)

    c = sub.add_parser("search")
    c.add_argument("query")
    c.add_argument("-k", type=int, default=5)
    c.add_argument("--no-rerank", action="store_true")
    c.set_defaults(fn=cmd_search)

    inc = sub.add_parser("incident").add_subparsers(dest="op", required=True)
    c = inc.add_parser("start")
    c.add_argument("--id", required=True)
    c.add_argument("--env", required=True, choices=["staging", "prod"])
    c.add_argument("--target", required=True)
    c.add_argument("--summary", required=True)
    c.add_argument("--proposal-file")
    c.add_argument("--approval-timeout", type=int, default=900)
    c.add_argument("--no-rerank", action="store_true")
    c.add_argument("--no-retrieval", action="store_true")
    c.add_argument("--wait", action="store_true")
    c.set_defaults(fn=cmd_incident_start)

    for name in ("approve", "reject"):
        c = sub.add_parser(name)
        c.add_argument("id")
        c.add_argument("--by", required=True)
        c.add_argument("--comment", default="")
        c.add_argument("--wait", action="store_true")
        c.set_defaults(fn=cmd_decide)

    for name, fn in (("status", cmd_status), ("audit", cmd_audit)):
        c = sub.add_parser(name)
        c.add_argument("id")
        c.set_defaults(fn=fn)

    admin = sub.add_parser("broker").add_subparsers(dest="op", required=True)
    for name in ("revoke", "restore", "transient"):
        c = admin.add_parser(name)
        c.add_argument("--action", default="*")
        if name == "transient":
            c.add_argument("--count", type=int)
        c.set_defaults(fn=cmd_broker)

    demo = sub.add_parser("demo").add_subparsers(dest="op", required=True)
    demo.add_parser("list").set_defaults(fn=cmd_demo)
    c = demo.add_parser("run")
    c.add_argument("name")
    c.set_defaults(fn=cmd_demo)

    fleet = sub.add_parser("fleet").add_subparsers(dest="op", required=True)
    c = fleet.add_parser("faults")
    c.add_argument("--action", default="*")
    c.add_argument("--fail-next", type=int, default=0)
    c.add_argument("--latency-ms", type=int, default=0)
    c.add_argument("--drop-next", type=int, default=0)
    c.add_argument("--clear", action="store_true")
    c.set_defaults(fn=cmd_fleet)
    c = fleet.add_parser("counters")
    c.add_argument("--clear", action="store_true")
    c.set_defaults(fn=cmd_fleet)
    c = fleet.add_parser("reset")
    c.set_defaults(fn=cmd_fleet)
    c = fleet.add_parser("state")
    c.add_argument("--env", required=True, choices=["staging", "prod"])
    c.add_argument("--target", required=True)
    c.set_defaults(fn=cmd_fleet)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    return asyncio.run(args.fn(get_settings(), args)) or 0


if __name__ == "__main__":
    sys.exit(main())
