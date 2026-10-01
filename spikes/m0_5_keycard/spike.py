import argparse
import json
import sys
import time
from pathlib import Path

import jwt
from dotenv import dotenv_values
from keycardai.oauth import Client
from keycardai.oauth.server.credentials import ClientSecret

ROOT = Path(__file__).resolve().parents[2]
ENV = dotenv_values(ROOT / ".env")
ISSUER = ENV["KEYCARD_ZONE_URL"].strip().rstrip("/")
CLIENT_ID = ENV["KEYCARD_CLIENT_ID"].strip()
HOST = ISSUER.split("//", 1)[1]
JWKS_URI = f"{ISSUER}/openidconnect/jwks"
ACTIONS = ["state", "restart", "scale", "pause", "reset-offset"]
RESOURCES = [f"urn:gax:fleet-api:{a}" for a in ACTIONS]
CLAIMS = ["iss", "aud", "sub", "client_id", "keycard_app_id", "scope", "target"]

mints = 0
client = Client(ISSUER, auth=ClientSecret((CLIENT_ID, ENV["KEYCARD_CLIENT_SECRET"].strip())).auth)
jwks = jwt.PyJWKClient(JWKS_URI, headers={"User-Agent": "gax-keycard-spike/1.0"})


def mask(v):
    s = json.dumps(v) if not isinstance(v, str) else v
    return s.replace(HOST, "<zone>").replace(CLIENT_ID, "<KEYCARD_CLIENT_ID>")


def mint(resource):
    global mints
    mints += 1
    t0 = time.perf_counter()
    try:
        tr = client.client_credentials_grant(resource=resource)
        return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "token": tr.access_token, "expires_in": tr.expires_in}
    except Exception as e:
        return {
            "ok": False,
            "ms": round((time.perf_counter() - t0) * 1000),
            "exc": type(e).__name__,
            "error": getattr(e, "error", None),
            "description": getattr(e, "error_description", None),
            "status": getattr(e, "status_code", None),
            "retryable": getattr(e, "retryable", None),
            "message": str(e),
        }


def claims_of(token):
    c = jwt.decode(token, options={"verify_signature": False})
    out = {k: c.get(k) for k in CLAIMS}
    out["jti_present"] = bool(c.get("jti"))
    out["exp_minus_iat"] = c["exp"] - c["iat"]
    out["alg"] = jwt.get_unverified_header(token).get("alg")
    return out


def verify(token, audience):
    try:
        key = jwks.get_signing_key_from_jwt(token).key
        jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER, audience=audience)
        return "valid"
    except Exception as e:
        return f"rejected:{type(e).__name__}"


def report(resource, r):
    out = {"resource": resource, "ms": r["ms"]}
    if r["ok"]:
        c = claims_of(r["token"])
        granted = [c["aud"]] if isinstance(c["aud"], str) else list(c["aud"] or [])
        target = c.get("target") or []
        granted += target if isinstance(target, list) else [target]
        out.update(outcome="issued", expires_in=r["expires_in"], claims=c,
                   resource_granted=resource in granted)
        out["verify_correct_aud"] = verify(r["token"], resource)
        out["verify_wrong_aud"] = verify(r["token"], "urn:gax:fleet-api:wrong")
    else:
        out.update(outcome="error", **{k: r[k] for k in ("exc", "error", "description", "status", "retryable", "message")})
    print(mask(json.dumps(out)), flush=True)
    return out


def denied(out, resource):
    return out["outcome"] == "error" or not out["resource_granted"]


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint")
    m.add_argument("resources", nargs="*", default=RESOURCES)
    q = sub.add_parser("poll")
    q.add_argument("resource")
    q.add_argument("--until", choices=["denied", "allowed"], required=True)
    q.add_argument("--interval", type=float, default=10)
    q.add_argument("--max", type=int, default=12)
    a = p.parse_args()

    if a.cmd == "mint":
        for res in a.resources:
            report(res, mint(res))
    else:
        t0 = time.monotonic()
        for i in range(1, a.max + 1):
            out = report(a.resource, mint(a.resource))
            if denied(out, a.resource) == (a.until == "denied"):
                print(json.dumps({"reached": a.until, "try": i, "elapsed_s": round(time.monotonic() - t0, 1)}))
                break
            if i < a.max:
                time.sleep(a.interval)
        else:
            print(json.dumps({"reached": None, "tries": a.max, "elapsed_s": round(time.monotonic() - t0, 1)}))
    print(json.dumps({"mints_this_run": mints}))


if __name__ == "__main__":
    sys.exit(main())
