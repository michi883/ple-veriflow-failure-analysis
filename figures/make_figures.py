"""Builds the three Writeup figures from the data in this repository. No model calls, no private files.

Run from the repository root:  uv run --with matplotlib python figures/make_figures.py
Every image is 1600 x 900 px (16:9), the shape the Kaggle media gallery shows without cropping.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "figures"
sys.path[:0] = [str(REPO / "experiments"), str(REPO / "analysis")]

import overstatement  # noqa: E402
import score_arms  # noqa: E402
import score_heldout  # noqa: E402
import trajectory_passes  # noqa: E402

DOMAIN = {"Civil": "#2f6f9f", "Electrical": "#d08a1f", "Mechanical": "#4c9a5b", "Structural": "#9b4f8f"}
CLASSES = [("no deliverable exported", "#7a1f1f"), ("deliverable graded 0", "#c0392b"), ("score below 0.40", "#e07b39"),
           ("score 0.40 to 0.80", "#e3c04a"), ("score 0.80 or higher", "#5a9e6f")]
SIZE = dict(figsize=(12.8, 7.2), dpi=125)
plt.rcParams.update({"font.size": 12, "font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False})


def dev_tasks() -> list[dict]:
    rows = list(csv.DictReader((REPO / "data/template_baseline/dev_tasks.csv").open()))
    for r in rows:
        r["reward"], r["agent_steps"], r["deliverable_files"] = float(r["reward"]), int(r["agent_steps"]), int(r["deliverable_files"])
        r["cls"] = (0 if r["deliverable_files"] == 0 else 1 if r["reward"] == 0 else 2 if r["reward"] < 0.4 else 3 if r["reward"] < 0.8 else 4)
    return rows


def guarded_run() -> dict:
    """Our one run of the submitted harness (template + Scope Guard) on engineers-estimate-check."""
    r = json.loads((REPO / "data/scope_guard_run/run.json").read_text())
    u, p = r["usage"], r["price_usd_per_mtok"]
    fresh = u["input_tokens"] - u["cache_read_tokens"]
    rest = u["cache_read_tokens"] * p["cache_read"] + u["output_tokens"] * p["output"]
    r["usd"] = ((fresh * p["input"] + rest) / 1e6, (fresh * p["cache_write"] + rest) / 1e6)
    return r


def starter_run() -> dict:
    r = json.loads((REPO / "data/veriflow_run/run.json").read_text())
    r["usd"] = (r["usage"]["input_tokens"] * 4.0 + r["usage"]["output_tokens"] * 20.0) / 1e6
    return r


def architecture() -> None:
    fig = plt.figure(**SIZE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9)
    ax.axis("off")
    ours, ours_edge = "#dff0e2", "#2e7d32"

    def box(x, y, w, h, text, fc, ec="#333333", fs=11.5, lw=1.4):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.12", fc=fc, ec=ec, lw=lw))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, linespacing=1.35)

    def arrow(x1, y1, x2, y2, text="", dy=0.16):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=16, lw=1.4, color="#333333"))
        if text:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + dy, text, ha="center", fontsize=10, color="#444444")

    g, d = guarded_run(), overstatement.decompose()
    ax.text(0.4, 8.4, "Figure 1. Architecture", fontsize=18, weight="bold")
    ax.text(0.4, 7.8, "A. Submitted entry: the organizers' template plus Scope Guard (green: ours)", fontsize=13.5, weight="bold")
    box(0.4, 6.3, 2.1, 1.15, "PLE evaluator\n(task, limits)", "#e8eef6", fs=11)
    box(3.0, 6.3, 2.9, 1.15, "serve.py + taskbridge.py\nWebSocket protocol", "#fff3d6", fs=11)
    box(6.4, 6.3, 2.5, 1.15, "harness.py\nruns Pi 1.1.0\n(--thinking max)", "#fff3d6", fs=11)
    box(9.5, 6.3, 3.0, 1.15, "Scope Guard\nsession proxy on the\ntool relay", ours, ec=ours_edge, fs=11, lw=2.2)
    box(13.1, 6.3, 2.5, 1.15, "task container\n(project files,\nMCP apps)", "#e8eef6", fs=11)
    arrow(2.5, 6.875, 3.0, 6.875)
    arrow(5.9, 6.875, 6.4, 6.875)
    arrow(8.9, 6.875, 9.5, 6.875, "tools")
    arrow(12.5, 6.875, 13.1, 6.875)
    box(5.95, 4.55, 3.4, 1.05, "model:\ngoogle-cloud/claude-opus-5-5", "#e8eef6", fs=10.5)
    arrow(7.65, 6.3, 7.65, 5.66)
    ax.text(0.4, 5.08, "Yellow and blue: the organizers' template, unchanged.\nWithout an explicit restriction in the instruction,\n"
            "the guard is inactive and the harness runs\nexactly as the template does.", fontsize=10.6, color="#444444",
            va="center", linespacing=1.4)
    box(9.9, 4.25, 5.7, 1.6,
        "Reads an explicit restriction from the instruction\n(\"Stay in Section 2\"). After each command it reads the\n"
        "tables of the files that changed. A row keyed outside\nthe allowed unit: a flag is appended to that command's\n"
        "output and, if it stands, to the final message.\nNo model call. It never blocks or edits.",
        ours, ec=ours_edge, fs=10.2, lw=1.6)
    arrow(11.0, 6.3, 11.0, 5.91)

    ax.text(0.4, 3.85, "B. Public starter we analyzed: veriflow-ple-starter (third-party code, not submitted)", fontsize=13.5, weight="bold")
    box(0.4, 2.3, 3.2, 1.25, "Planner\n'read-only' by instruction,\nbut keeps a shell", "#fde2e2", ec="#b3261e", fs=10.8)
    box(4.2, 2.3, 3.0, 1.25, "Worker\nwrites the deliverable,\nlogs numeric claims", "#f2e6f0", fs=10.8)
    box(7.8, 2.3, 3.7, 1.25, "verify_claims\nre-computes the agent's own\nexpression over its own inputs", "#fde2e2", ec="#b3261e", fs=10.8)
    box(12.1, 2.3, 3.5, 1.25, "'Cold-read' verifier\nis given the worker's summary;\nchecks hard gates and files", "#fde2e2", ec="#b3261e", fs=10.8)
    arrow(3.6, 2.925, 4.2, 2.925)
    arrow(7.2, 2.925, 7.8, 2.925)
    arrow(11.5, 2.925, 12.1, 2.925)
    ax.text(0.4, 1.72, "The dev task engineers-estimate-check, two runs of Claude Opus 5.5:", fontsize=12.5, weight="bold")
    ax.text(0.4, 1.05,
            f"Starter (B): all {len(d['lines'])} changed lines passed the arithmetic check; {d['unlicensed_changes']} were changes no instruction rule "
            f"allowed (+${d['overstatement']:,.2f}). Score 0.543.",
            fontsize=11.8, color="#b3261e")
    ax.text(0.4, 0.42,
            f"Submitted entry (A): guard active, {g['guard']['log_lines_flagged']} flags. The agent keyed no row outside Section 2. "
            f"Score {g['grading']['reward']:.1f}. One run: not an effect of the guard.",
            fontsize=11.8, color=ours_edge)
    fig.savefig(OUT / "fig1_architecture.png")
    plt.close(fig)


def score_vs_cost() -> None:
    rows = dev_tasks()
    arms, held = score_arms.score(), score_heldout.score()
    g, st = guarded_run(), starter_run()
    fig, (a1, a2) = plt.subplots(1, 2, **SIZE, gridspec_kw={"width_ratios": [1.2, 1]})
    fig.suptitle("Figure 2. Score against compute", x=0.03, y=0.975, ha="left", fontsize=17, weight="bold")
    for name, col in DOMAIN.items():
        pts = [r for r in rows if r["domain"] == name]
        a1.scatter([r["agent_steps"] for r in pts], [r["reward"] for r in pts], s=70, color=col, label=name, edgecolor="white", zorder=3)
    for lo, hi in ((0, 65), (66, 100), (101, 10 ** 6)):
        grp = [r for r in rows if lo <= r["agent_steps"] <= hi]
        m = sum(r["reward"] for r in grp) / len(grp)
        x0, x1 = max(lo, min(r["agent_steps"] for r in grp) - 3), min(hi, max(r["agent_steps"] for r in grp) + 3)
        a1.plot([x0, x1], [m, m], color="#222222", lw=2.0, zorder=2)
        a1.text(x0, m + 0.03, f"mean {m:.2f} (n={len(grp)})", ha="left", fontsize=10, color="#222222", weight="bold")
    est = next(r for r in rows if r["task"] == "engineers-estimate-check")
    a1.scatter([est["agent_steps"]], [est["reward"]], s=260, facecolor="none", edgecolor="#111111", lw=1.8, zorder=4)
    a1.scatter([g["usage"]["model_calls"]], [g["grading"]["reward"]], s=330, marker="*", color="#2e7d32", edgecolor="white", zorder=5)
    a1.annotate(f"our run, template + Scope Guard: {g['grading']['reward']:.1f}\n{g['usage']['model_calls']} model calls, "
                f"about \\${sum(g['usd']) / 2:.0f}", (g["usage"]["model_calls"], g["grading"]["reward"]),
                textcoords="offset points", xytext=(-18, 16), fontsize=10.5, color="#2e7d32", weight="bold")
    a1.annotate(f"same task, unmodified template:\n{est['reward']:.3f} in {est['agent_steps']} steps (cost not recorded)",
                (est["agent_steps"], est["reward"]), textcoords="offset points", xytext=(-52, -72), fontsize=10,
                arrowprops=dict(arrowstyle="-", color="#555555", lw=0.9))
    a1.set_xlabel("agent steps (model turns) per task")
    a1.set_ylabel("dev-task score")
    a1.set_title("A. Organizers' run of the unmodified template (29 tasks, dots)\nand our one run of the submitted harness (star)",
                 fontsize=12.5, loc="left")
    a1.legend(frameon=False, fontsize=10, loc="upper right", ncol=2)
    a1.set_ylim(-0.06, 1.42)

    pts = [("baseline", arms["calls"]["baseline"], arms["totals"]["baseline"], "#666666", "o", (10, -4)),
           ("+ 'justify or keep' sentence", arms["calls"]["plain"], arms["totals"]["plain"], "#2f6f9f", "o", (10, -4)),
           ("+ rule protocol", arms["calls"]["gate"], arms["totals"]["protocol"], "#9b4f8f", "o", (10, 2)),
           ("held-out baseline", held["calls"]["baseline"], f"{held['by_task']['all']['baseline']}/25", "#666666", "s", (10, -18)),
           ("held-out frozen gate", held["calls"]["gate"], f"{held['by_task']['all']['gate']}/25", "#b3261e", "s", (-10, -22))]
    for label, c, frac, col, marker, off in pts:
        num, den = (int(v) for v in frac.split("/"))
        x = c["usd_per_decision_cached_prefix"]
        a2.scatter(x, num / den, s=110, color=col, marker=marker, edgecolor="white", zorder=3)
        a2.annotate(f"{label} ({frac})", (x, num / den), textcoords="offset points", xytext=off, fontsize=10.5,
                    ha="right" if off[0] < 0 else "left")
    a2.set_xlabel("measured $ per decision (evidence prefix cached)")
    a2.set_ylabel("decisions matching the hidden reference")
    a2.set_title("B. Our decision-level experiments (Claude Opus 5.5):\nthe rule gate doubled cost and did not help held out",
                 fontsize=12.5, loc="left")
    a2.set_ylim(0.62, 1.0)
    a2.set_xlim(0.015, 0.095)
    a2.text(0.017, 0.635, "circles: estimate-check, 9 decisions x 3 samples (in-sample)\nsquares: 2 unseen tasks, 25 decisions x 1 sample (held out)\n"
            f"For scale: the starter's run of the task cost \\${st['usd']:.0f} for 0.543.", fontsize=10, color="#555555")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUT / "fig2_score_vs_cost.png")
    plt.close(fig)


def failure_breakdown() -> None:
    rows = sorted(dev_tasks(), key=lambda r: r["reward"])
    d, g = overstatement.decompose(), guarded_run()
    fig, (a1, a2) = plt.subplots(1, 2, **SIZE, gridspec_kw={"width_ratios": [1.25, 1]})
    fig.suptitle("Figure 3. Failure breakdown on the 29 dev tasks", x=0.03, y=0.975, ha="left", fontsize=17, weight="bold")
    y = list(range(len(rows)))
    a1.barh(y, [1 - r["reward"] for r in rows], color=[CLASSES[r["cls"]][1] for r in rows])
    a1.set_yticks(y)
    a1.set_yticklabels([r["task"] for r in rows], fontsize=8.6)
    a1.invert_yaxis()
    for i, r in enumerate(rows):
        a1.text(1 - r["reward"] + 0.012, i, f"{r['reward']:.2f}", va="center", fontsize=8.2, color="#333333")
        if r["task"] == g["task"]:
            a1.get_yticklabels()[i].set_weight("bold")
            a1.text(1 - r["reward"] + 0.115, i, f"our run, submitted harness: {g['grading']['reward']:.2f} (the only task we ran)",
                    va="center", fontsize=8.6, color="#2e7d32", weight="bold")
    total = sum(1 - r["reward"] for r in rows)
    handles = []
    for k, (name, col) in enumerate(CLASSES):
        grp = [r for r in rows if r["cls"] == k]
        lost = sum(1 - r["reward"] for r in grp)
        handles.append(Patch(color=col, label=f"{name}: {len(grp)} tasks, {lost:.1f} points lost ({lost / total:.0%})"))
    fig.legend(handles=handles, frameon=False, fontsize=10, loc="lower left", bbox_to_anchor=(0.03, 0.005), ncol=2, columnspacing=1.5)
    a1.set_xlim(0, 1.16)
    a1.set_xlabel("points lost per task (1 - score); labels show the score")
    a1.set_title(f"A. Organizers' run of the unmodified template (their artifacts):\n{total:.1f} of 29 points lost. "
                 f"Our harness ran {g['dev_set']['tasks_with_artifacts']} of these 29 tasks.", fontsize=12, loc="left")

    parts = sorted(((c, v) for c, v in d["by_cause"].items() if abs(v) >= 1), key=lambda kv: -kv[1])
    labels = {"substitute item priced where the rule says flag": "Ramp and path priced at a\nsubstitute item (rule: flag it)",
              "proxy quantity: storage volume used as excavation": "Storage volume used as the\nexcavation quantity (rule: keep)",
              "over-reading: pipe length re-derived from a curve radius": "Pipe lengths re-derived from a\ncurve radius (rule: keep)"}
    cols = ["#b3261e", "#e07b39", "#2f6f9f"]
    a2.barh(range(len(parts)), [v for _, v in parts], color=cols[:len(parts)])
    a2.set_yticks(range(len(parts)))
    a2.set_yticklabels([labels[c] for c, _ in parts], fontsize=10.5)
    a2.invert_yaxis()
    a2.axvline(0, color="#333333", lw=0.9)
    for i, (_, v) in enumerate(parts):
        a2.text(max(v, 0) + 90, i, f"{v:+,.0f} $", va="center", fontsize=11)
    a2.set_xlim(-700, 7600)
    a2.set_xlabel("effect on the Section 2 subtotal (\\$)")
    a2.text(1.0, -0.19, "Scope Guard checks only the first cause (red):\nrows keyed outside the section the instruction\n"
            "names. In our run of the submitted harness none\nof the three occurred; the guard raised no flag.",
            transform=a2.transAxes, ha="right", va="top", fontsize=10.2, color="#2e7d32", linespacing=1.35).set_in_layout(False)
    a2.set_title(f"B. engineers-estimate-check, our run of the public starter:\n{d['unlicensed_changes']} unlicensed changes made the subtotal "
                 f"${d['overstatement']:,.2f} too high\n(21 of 46 rubric points lost). All {len(d['lines'])} changed lines passed\nthe arithmetic check.",
                 fontsize=12, loc="right", ha="right")
    fig.tight_layout(rect=(0, 0.13, 1, 0.94))
    fig.savefig(OUT / "fig3_failure_breakdown.png")
    plt.close(fig)


def card() -> None:
    """The 2:1 card and thumbnail image."""
    fig = plt.figure(figsize=(11.2, 5.6), dpi=100)
    fig.patch.set_facecolor("#14213d")
    fig.text(0.06, 0.80, "Scope Guard", color="white", fontsize=44, weight="bold", va="center")
    fig.text(0.06, 0.59, "Deterministic enforcement of\nexplicit task restrictions", color="white", fontsize=26, va="center",
             linespacing=1.3)
    fig.text(0.06, 0.33, "A harness check with no model call, built from a failure where an agent\n"
             "got every number right and still broke the task's rules", color="#e5e5e5", fontsize=15, va="center", linespacing=1.4)
    fig.text(0.06, 0.11, "Entry: the organizers' PLE template + Scope Guard. 1 of 29 dev tasks run (1.0); the guard raised no flag.",
             color="#fca311", fontsize=12.5)
    fig.savefig(OUT / "card.png", facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    card()
    architecture()
    score_vs_cost()
    failure_breakdown()
    print(sorted(p.name for p in OUT.glob("*.png")))
