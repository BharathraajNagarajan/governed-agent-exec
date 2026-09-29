import json
import time
from typing import Literal, Optional
import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError
from pymongo import MongoClient

load_dotenv("../../.env")
MODEL = "claude-opus-5-5"


class ActionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: Optional[int] = None


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["restart_consumer", "scale_consumer", "pause_pipeline", "reset_consumer_offset"]
    target: str
    environment: Literal["staging", "prod"]
    params: ActionParams
    justification: str
    cited_chunk_ids: list[str]


class MalformedProposal(Exception):
    def __init__(self, raw, error):
        super().__init__(f"{error.error_count()} validation error(s)")
        self.raw = raw
        self.error = error


def validate(raw: str) -> ActionProposal:
    try:
        return ActionProposal.model_validate_json(raw)
    except ValidationError as e:
        raise MalformedProposal(raw, e) from e


chunks = list(MongoClient("mongodb://localhost:27017/?directConnection=true")["m0_spike"]["voyage_chunks"].find({}, {"text": 1}))
context = "\n".join(f"[{c['_id']}] {c['text']}" for c in chunks)
incident = "Incident INC-42 (staging): consumer group orders-consumer has 0 active members and lag is climbing on topic orders."

t = time.perf_counter()
resp = anthropic.Anthropic().messages.parse(
    model=MODEL,
    max_tokens=16000,
    output_config={"effort": "low"},
    system="You propose exactly one remediation action for an incident. Cite the runbook chunk ids you relied on.",
    messages=[{"role": "user", "content": f"Runbook:\n{context}\n\n{incident}"}],
    output_format=ActionProposal,
)
print(f"model={resp.model} stop_reason={resp.stop_reason} latency_ms={(time.perf_counter() - t) * 1000:.0f} usage=in:{resp.usage.input_tokens} out:{resp.usage.output_tokens}")
raw = next(b.text for b in resp.content if b.type == "text")
proposal = validate(raw)
assert proposal == resp.parsed_output
print("VALID", proposal.model_dump_json())

for name, bad in [
    ("unknown_action", json.dumps({**proposal.model_dump(), "action": "drop_topic"})),
    ("missing_field", json.dumps({k: v for k, v in proposal.model_dump().items() if k != "environment"})),
    ("not_json", "Sure! I think you should restart the consumer."),
]:
    try:
        validate(bad)
        print("UNEXPECTED PASS", name)
    except MalformedProposal as e:
        print(f"MALFORMED {name}: {type(e).__name__} -> {[(err['type'], err['loc']) for err in e.error.errors()]}")
