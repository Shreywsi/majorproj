"""Tables, bootstrap CIs and figures from results/raw.csv (Step 7-8).

    python analyze.py                       # reads results/raw.csv
    python analyze.py --raw results/raw.csv --rho 0.3 --asr-target 0.05
"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import ORACLE, VERSION_NAMES

METRICS = ["mean_y", "asr", "dq", "far", "detect_rate", "dl", "rt", "ms_per_round",
           "bad_share", "viol_rate", "recovery_rate"]


def boot_ci(x, n=2000, seed=0):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return x.mean(), *np.percentile(means, [2.5, 97.5])


def summary_table(raw):
    rows = []
    for (atk, v, rho), g in raw.groupby(["attack", "version", "rho"]):
        row = dict(attack=atk, version=v, rho=rho, n_seeds=len(g))
        for m in METRICS:
            if m in g:
                mean, lo, hi = boot_ci(g[m])
                row.update({m: mean, m + "_lo": lo, m + "_hi": hi})
        rows.append(row)
    s = pd.DataFrame(rows)
    # overhead relative to R0 of the same attack/rho
    base = s[s.version == 0].set_index(["attack", "rho"]).ms_per_round
    s["overhead"] = [r.ms_per_round / base.get((r.attack, r.rho), np.nan) for r in s.itertuples()]
    return s


def heatmap(s, rho, out, value="asr", title="Attack success rate (ASR)", cmap="magma_r"):
    d = s[(s.rho == rho) | (s.attack == "none")]
    d = d[d.attack != "none"]
    versions = sorted(v for v in d.version.unique() if v != ORACLE)
    attacks = list(d.attack.unique())
    M = np.array([[d[(d.attack == a) & (d.version == v)][value].mean() for v in versions]
                  for a in attacks])
    fig, ax = plt.subplots(figsize=(1.1 * len(versions) + 3, 0.5 * len(attacks) + 2))
    im = ax.imshow(M, cmap=cmap, vmin=0, vmax=max(0.6, np.nanmax(M)), aspect="auto")
    ax.set_xticks(range(len(versions)), [f"R{v}" for v in versions])
    ax.set_yticks(range(len(attacks)), attacks)
    for i in range(len(attacks)):
        for j in range(len(versions)):
            val = M[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=8,
                        color="white" if val > 0.3 else "black")
    ax.set_title(f"{title}, attacker ratio rho={rho}")
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def quality_vs_rho(s, out, attacks=("liar", "twofaced", "sybil", "colluder", "injector", "sleeper"),
                   versions=(0, 1, 5, 7, 8, ORACLE)):
    d = s[s.attack.isin(attacks)]
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.5), sharey=True)
    for ax, atk in zip(axes.ravel(), attacks):
        for v in versions:
            g = d[(d.attack == atk) & (d.version == v)].sort_values("rho")
            if len(g):
                ax.errorbar(g.rho, g.mean_y, yerr=[g.mean_y - g.mean_y_lo, g.mean_y_hi - g.mean_y],
                            marker="o", ms=3, capsize=2, label=VERSION_NAMES[v])
        ax.set_title(atk)
        ax.set_xlabel("attacker ratio rho")
        ax.grid(alpha=0.3)
    axes[0, 0].set_ylabel("mean output quality")
    axes[0, 0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def smallest_defence(s, target):
    rows = []
    for (atk, rho), g in s[s.attack != "none"].groupby(["attack", "rho"]):
        g = g[g.version.isin(range(1, 9))].sort_values("version")
        ok = g[g.asr < target]
        rows.append(dict(attack=atk, rho=rho, smallest_version=int(ok.version.min()) if len(ok) else None,
                         asr_R7=g[g.version == 7].asr.mean(), asr_R8=g[g.version == 8].asr.mean()))
    t = pd.DataFrame(rows)
    t["still_succeeds_vs_R7_R8"] = (t.asr_R7 >= target) & (t.asr_R8 >= target)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="results/raw.csv")
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--rho", type=float, default=0.3, help="rho for the heatmaps")
    ap.add_argument("--asr-target", type=float, default=0.05)
    a = ap.parse_args()

    raw = pd.read_csv(a.raw)
    os.makedirs(a.outdir, exist_ok=True)
    s = summary_table(raw)
    s.to_csv(f"{a.outdir}/summary.csv", index=False)

    rhos = sorted(r for r in raw.rho.unique() if r > 0)
    rho = a.rho if a.rho in rhos else rhos[0]
    heatmap(s, rho, f"{a.outdir}/asr_heatmap.png")
    heatmap(s, rho, f"{a.outdir}/quality_drop_heatmap.png", value="dq",
            title="Quality drop (q* - y)", cmap="viridis_r")
    quality_vs_rho(s, f"{a.outdir}/quality_vs_rho.png")
    t = smallest_defence(s, a.asr_target)
    t.to_csv(f"{a.outdir}/smallest_defence.csv", index=False)

    print(f"\n== Step 2 baseline check (liar, rho={rho}) ==")
    b = s[(s.attack == "liar") & (s.rho == rho) & s.version.isin([0, 1, 2])].set_index("version").mean_y
    if {0, 1, 2} <= set(b.index):
        print(f"R1={b[1]:.3f} < R0={b[0]:.3f} < R2={b[2]:.3f}  ->  {b[1] < b[0] < b[2]}")
    print(f"\n== Smallest R_i with ASR < {a.asr_target} (rho={rho}) ==")
    print(t[t.rho == rho].to_string(index=False))
    far = s[(s.version == 7) & (s.attack == "none")][["far", "far_lo", "far_hi"]]
    if len(far):
        print("\nR7 false-accusation rate with no attackers:", far.round(3).to_dict("records")[0])
    print(f"\nWrote tables and figures to {a.outdir}/")


if __name__ == "__main__":
    main()
