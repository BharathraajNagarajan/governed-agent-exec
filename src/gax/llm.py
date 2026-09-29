import json
from dataclasses import dataclass
import anthropic
from pydantic import ValidationError
from gax.models import ActionProposal, Incident, MalformedProposal, parse_proposal

SYSTEM = (
    "You are the remediation proposer for a streaming data platform. "
    "Propose exactly one action for the incident, chosen from: restart_consumer, scale_consumer (params.replicas required), "
    "pause_pipeline, reset_consumer_offset. Use the incident's target and environment. "
    "Base the proposal on the runbook excerpts and cite the chunk ids you relied on. "
    "Your proposal is evaluated by a separate policy and may be denied; do not reason about permissions."
)


@dataclass
class ProposalResult:
    proposal: ActionProposal
    model: str
    stop_reason: str
    input_tokens: int
    output_tokens: int


def render_prompt(incident: Incident, chunks: list[dict]) -> str:
    context = "\n\n".join(f"[{c['chunk_id']}] {c['text']}" for c in chunks) or "(no runbook context)"
    return (
        f"Runbook excerpts:\n{context}\n\n"
        f"Incident {incident.incident_id} ({incident.environment}), target {incident.target}:\n{incident.summary}"
    )


def propose(client: anthropic.Anthropic, model: str, incident: Incident, chunks: list[dict]) -> ProposalResult:
    try:
        resp = client.messages.parse(
            model=model,
            max_tokens=4000,
            output_config={"effort": "low"},
            system=SYSTEM,
            messages=[{"role": "user", "content": render_prompt(incident, chunks)}],
            output_format=ActionProposal,
        )
    except ValidationError as e:
        raise MalformedProposal(json.dumps(e.errors(include_input=False, include_url=False)), e) from e
    raw = next((b.text for b in resp.content if b.type == "text"), "")
    if resp.stop_reason != "end_turn":
        raw = raw or f"stop_reason={resp.stop_reason}"
    proposal = parse_proposal(raw)
    return ProposalResult(proposal, resp.model, resp.stop_reason, resp.usage.input_tokens, resp.usage.output_tokens)
