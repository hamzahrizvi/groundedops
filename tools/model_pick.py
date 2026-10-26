"""Keep the model picker's choice inside each agent's floor and ceiling, and log it.

  python tools/model_pick.py <agent> <job> "<model-picker's reply line>" [--ops OPS]
  python tools/model_pick.py --selftest

Prints "MODEL <model> EFFORT <effort>" for the Agent call. The ceiling is the agent's own
model and effort (.claude/agents/<agent>.md front matter); the floor is FLOOR below, by job
first, then by agent. A reply that does not parse gets the ceiling: an unclear pick never
makes a job weaker. Every pick is appended to OPS/state/model_picks.log, so Audit can see
whether lighter picks cost quality.
"""
import argparse
import datetime
import os
import re
import sys
import tempfile

MODELS = ["haiku", "sonnet", "opus", "fable"]
EFFORTS = ["low", "medium", "high", "xhigh"]
# ponytail: floors set by hand on 2026-10-13; raise one if Audit finds lighter picks losing quality
FLOOR = {"product-owner": ("sonnet", "medium"), "product-owner:diary": ("haiku", "low"),
         "dev": ("sonnet", "medium"), "dev-junior": ("sonnet", "low"), "security": ("sonnet", "medium"),
         "rnd": ("sonnet", "medium"), "customer": ("sonnet", "low"), "marketing": ("haiku", "medium"),
         "audit": ("haiku", "low")}
REPLY = re.compile(r"MODEL\s+(\w+)\s+EFFORT\s+(\w+)", re.I)
AGENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".claude", "agents")


def ceiling(agent, agents=AGENTS):
    head = open(os.path.join(agents, agent + ".md"), encoding="utf-8").read().split("---")[1]
    get = lambda k: re.search(rf"(?m)^{k}:\s*(\w+)", head)[1]
    return get("model"), get("effort")


def clamp(agent, job, reply, agents=AGENTS):
    top = ceiling(agent, agents)
    low = FLOOR.get(f"{agent}:{job.lower()}", FLOOR.get(agent, top))
    m = REPLY.search(reply or "")
    if not m or m[1].lower() not in MODELS or m[2].lower() not in EFFORTS:
        return top
    out = []
    for scale, want, lo, hi in ((MODELS, m[1].lower(), low[0], top[0]), (EFFORTS, m[2].lower(), low[1], top[1])):
        i, lo_i, hi_i = scale.index(want), scale.index(lo), scale.index(hi)
        out.append(scale[max(min(lo_i, hi_i), min(i, hi_i))])
    return tuple(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", nargs="?")
    ap.add_argument("job", nargs="?")
    ap.add_argument("reply", nargs="?", default="")
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.agent:
        return selftest()
    model, effort = clamp(a.agent, a.job or "", a.reply)
    os.makedirs(os.path.join(a.ops, "state"), exist_ok=True)
    with open(os.path.join(a.ops, "state", "model_picks.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M}\t{a.agent}\t{a.job}\t{model}\t{effort}\t{a.reply.strip()[:160]}\n")
    print(f"MODEL {model} EFFORT {effort}")


def selftest():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "product-owner.md"), "w").write("---\nname: product-owner\nmodel: fable\neffort: high\n---\nx")
        open(os.path.join(d, "audit.md"), "w").write("---\nname: audit\nmodel: sonnet\neffort: low\n---\nx")
        c = lambda *a: clamp(*a, agents=d)
        assert c("product-owner", "triage", "MODEL haiku EFFORT low | two findings") == ("sonnet", "medium"), "floor"
        assert c("product-owner", "diary", "MODEL haiku EFFORT low | facts only") == ("haiku", "low"), "job floor"
        assert c("product-owner", "triage", "MODEL opus EFFORT xhigh | many") == ("opus", "high"), "effort ceiling"
        assert c("audit", "weekly", "MODEL fable EFFORT high | ?") == ("sonnet", "low"), "ceiling"
        assert c("audit", "weekly", "I think sonnet") == ("sonnet", "low"), "no parse: the agent's own"
        assert c("product-owner", "triage", "MODEL gpt EFFORT low") == ("fable", "high"), "unknown model: the agent's own"
    print("selftest ok")


if __name__ == "__main__":
    sys.exit(main())
