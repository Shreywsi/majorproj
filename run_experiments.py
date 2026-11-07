"""Attack x router x rho x seed grid (Steps 3-7 of the experimental protocol).

Examples
    python run_experiments.py --quick                 # ~1 min smoke run
    python run_experiments.py                         # T=2000, 20 seeds
    python run_experiments.py --T 5000 --seeds 30     # final runs
    python run_experiments.py --attacks liar sleeper --versions 1 5 7
    python run_experiments.py --contracts --out results/raw_contracts.csv
"""
import argparse
import itertools
import json
import os
import subprocess
from multiprocessing import Pool, cpu_count

import pandas as pd
from tqdm import tqdm

from config import ATTACK_NAMES, Config, ORACLE
from metrics import summarize
from simulate import run

COMBOS = {"combo_adaptive_sybil_colluder": ["adaptive", "sybil", "colluder"]}
DEFAULT_VERSIONS = [0, 1, 2, 3, 4, 5, 6, 7, 8, ORACLE]


def attack_list(name):
    if name == "none":
        return []
    return COMBOS.get(name, [name])


def job(args):
    version, attack, rho, seed, cfg_dict, use_contracts = args
    cfg = Config(**cfg_dict)
    res = run(version, attack_list(attack), rho, seed, cfg, use_contracts)
    return dict(version=version, attack=attack, rho=rho, seed=seed, **summarize(res, cfg))


def git_hash():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "no-git"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--T", type=int, default=5000)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--rhos", type=float, nargs="+", default=[0.1, 0.3, 0.5])
    ap.add_argument("--versions", type=int, nargs="+", default=DEFAULT_VERSIONS)
    ap.add_argument("--attacks", nargs="+", default=["none"] + ATTACK_NAMES + list(COMBOS))
    ap.add_argument("--sleeper-rate", type=float, default=None)
    ap.add_argument("--contracts", action="store_true", help="enable LDP contract validation")
    ap.add_argument("--no-crypto", action="store_true", help="skip Ed25519 (faster)")
    ap.add_argument("--workers", type=int, default=max(1, cpu_count() - 1))
    ap.add_argument("--out", default="results/raw.csv")
    ap.add_argument("--quick", action="store_true", help="T=500, 3 seeds, rho=0.3")
    a = ap.parse_args()

    if a.quick:
        a.T, a.seeds, a.rhos = 500, 3, [0.3]
    cfg = Config(T=a.T, real_crypto=not a.no_crypto)
    if a.sleeper_rate is not None:
        cfg.sleeper_rate = a.sleeper_rate

    grid = []
    for attack, v, s in itertools.product(a.attacks, a.versions, range(a.seeds)):
        rhos = [0.0] if attack == "none" else a.rhos
        grid += [(v, attack, rho, s, cfg.to_dict(), a.contracts) for rho in rhos]

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    print(f"{len(grid)} runs, T={cfg.T}, workers={a.workers}")
    with Pool(a.workers) as p:
        rows = list(tqdm(p.imap_unordered(job, grid, chunksize=4), total=len(grid)))
    pd.DataFrame(rows).to_csv(a.out, index=False)
    with open(os.path.splitext(a.out)[0] + "_config.json", "w") as f:
        json.dump(dict(config=cfg.to_dict(), args=vars(a), git=git_hash()), f, indent=2)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
