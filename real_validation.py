"""Optional Phase 4: validate the provenance paradox with real Claude models.

Mirrors Sec. 5.4 of the base paper (Prakash, arXiv:2603.18043) but replaces the
paper's oracle "attested" condition by something a deployed router could do:

  blind          pick a delegate at random
  self_claimed   pick the delegate with the highest self-reported score
  runtime        warm up: run every delegate on a few probe tasks, score them
                 with an independent judge, then route to the best measured one

Delegates (all claim scores are fixed numbers, like in the paper):
  strong    claude-sonnet-5-5                       claims 0.85
  standard  claude-haiku-5-5                        claims 0.80
  degraded  claude-haiku-5-5 + "1-2 sentences" prompt, claims 0.95  (inflated)

Usage
    pip install anthropic
    set ANTHROPIC_API_KEY=...           (Windows)   /  export ANTHROPIC_API_KEY=...
    python real_validation.py --n-tasks 20 --n-probe 5

Cost: roughly 3 delegates x (n_tasks + n_probe) answers + judge calls, i.e. a few
dollars at most.  The API key is read from the environment, never from code.
NOTE: this script was written against the public Messages API but could not be
executed in the authoring sandbox (no API key); run `--dry-run` first.
"""
import argparse
import os
import random
import re

import numpy as np
import pandas as pd

STRONG = os.environ.get("STRONG_MODEL", "claude-sonnet-5-5")
CHEAP = os.environ.get("CHEAP_MODEL", "claude-haiku-5-5")
JUDGE = os.environ.get("JUDGE_MODEL", "claude-sonnet-5-5")

DELEGATES = {
    "strong": dict(model=STRONG, system="You are a careful assistant. Reason step by step, then answer.",
                   claim=0.85),
    "standard": dict(model=CHEAP, system="You are a helpful assistant. Reason briefly, then answer.",
                     claim=0.80),
    "degraded": dict(model=CHEAP, system="Answer in at most two sentences. Do not show reasoning.",
                     claim=0.95),
}

TASKS = [
    "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much is the ball?",
    "If 5 machines take 5 minutes to make 5 widgets, how long do 100 machines take to make 100 widgets?",
    "Lily pads double in area every day. If they cover a lake in 48 days, on which day do they cover half?",
    "All roses are flowers. Some flowers fade quickly. Can we conclude some roses fade quickly? Explain.",
    "Three people share a $30 bill but each pays $10 and gets $1 back from $5 of overcharge. Where is the 'missing' dollar?",
    "A farmer has 17 sheep; all but 9 run away. How many are left? Explain.",
    "You have two ropes that each burn in 60 minutes unevenly. How do you measure 45 minutes?",
    "What is the probability of at least one six when rolling two fair dice? Show the working.",
    "Alice is taller than Bob, Bob is taller than Carol, Dave is shorter than Carol. Who is the shortest?",
    "Is the statement 'This sentence is false' true or false? Explain briefly.",
    "A train leaves at 3pm at 60 km/h, another leaves the same station at 4pm at 90 km/h. When does the second catch up?",
    "How many ways can 4 distinct books be arranged on a shelf if two specific books must be adjacent?",
    "If today is Wednesday, what day will it be 100 days from now?",
    "A sequence goes 2, 6, 12, 20, 30. What comes next and why?",
    "Explain why the sum of two odd integers is always even.",
    "Two coins are flipped. At least one is heads. What is the probability both are heads?",
    "A clock shows 3:15. What is the angle between the hour and minute hands?",
    "You are in a room with two doors, one guard always lies and one always tells the truth. What single question finds the safe door?",
    "If it takes 8 people 6 days to build a wall, how many days do 12 people need?",
    "Why is 0.999... equal to 1? Give a short argument.",
    "Is 91 prime? Justify.",
    "Find the next letter: J, F, M, A, M, J, J, A, ...",
    "A shirt costs $40 after a 20% discount. What was the original price?",
    "What is the minimum number of weighings to find one heavier coin among 9 using a balance scale?",
    "Does 'no cats are dogs' imply 'no dogs are cats'? Explain.",
]


def ask(client, model, system, prompt, max_tokens=600):
    msg = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                 messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def judge(client, task, answer):
    prompt = (f"Task:\n{task}\n\nAnswer:\n{answer}\n\n"
              "Rate the answer's correctness and quality of reasoning from 1 (poor) to 10 (excellent). "
              "Reply with the integer only.")
    out = ask(client, JUDGE, "You are a strict, impartial grader.", prompt, max_tokens=8)
    m = re.search(r"\d+", out)
    return float(min(10, max(1, int(m.group())))) if m else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-tasks", type=int, default=20)
    ap.add_argument("--n-probe", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="print the plan, make no API calls")
    ap.add_argument("--out", default="results/real_validation.csv")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    tasks = TASKS[:]
    rng.shuffle(tasks)
    probe, evalset = tasks[:a.n_probe], tasks[a.n_probe:a.n_probe + a.n_tasks]
    names = list(DELEGATES)
    print(f"{len(probe)} probe tasks, {len(evalset)} evaluation tasks, delegates: {names}")
    if a.dry_run:
        return

    if "ANTHROPIC_API_KEY" not in os.environ:
        raise SystemExit("Set the ANTHROPIC_API_KEY environment variable first.")
    import anthropic
    client = anthropic.Anthropic()

    def run_delegate(name, task):
        d = DELEGATES[name]
        return judge(client, task, ask(client, d["model"], d["system"], task))

    # warm-up: measured ("runtime_observed") quality per delegate
    measured = {n: float(np.nanmean([run_delegate(n, t) for t in probe])) for n in names}
    best_measured = max(measured, key=measured.get)
    best_claimed = max(names, key=lambda n: DELEGATES[n]["claim"])
    print("measured on probes:", measured, "| best by claim:", best_claimed)

    rows = []
    for t in evalset:
        for cond, pick in (("blind", rng.choice(names)),
                           ("self_claimed", best_claimed),
                           ("runtime_observed", best_measured)):
            rows.append(dict(condition=cond, delegate=pick, task=t, score=run_delegate(pick, t)))
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    df.to_csv(a.out, index=False)
    print(df.groupby("condition").score.agg(["mean", "std", "count"]).round(2))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
