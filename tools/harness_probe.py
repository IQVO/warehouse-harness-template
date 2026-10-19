#!/usr/bin/env python3
"""harness_probe.py: Propose-Probe-Compose for ONE guide file (harness-template v4).

Google-Research's Harness^2 (arXiv:2610.14224) edits an agent's harness live, during a task, and keeps an
edit only if it measurably changes behaviour on THAT task -- no ground truth needed, just a contrastive
probe against the unedited baseline. This fleet's harness only ever changed on a human's incident-review
cadence (INCIDENTS.md -> tools/migrate_v3.py rollout); nothing tested whether a candidate wording of a
guide actually works before it shipped fleet-wide.

This script closes that gap for the narrow question EVALS.md rounds 2/3 left open: "a docs convention that
is only a sentence in a skill is followed about half the time by everyone... make the staleness a sensor
rather than spending more on prompts." Before building that sensor, ask the cheaper question first: does
REWORDING the guide change the rate at all? If not, EVALS.md's own conclusion already tells you to stop
spending on prose and build the sensor instead -- this script is what makes that an answer instead of a guess.

Propose:  --candidate <file> replaces ONE guide path (--guide, relative to repo root) in a disposable clone.
          The baseline variant is the SAME disposable clone with today's guide content -- both start from
          the full harness (guides+hooks+sensors); only --guide differs between the two variants.
Probe:    --runs N executions of --task per variant, interleaved (run i of baseline, then run i of
          candidate, before anyone runs i+1) -- reuses harness_eval2.py's PROMPTS, gates() and
          scan_transcript() so a probe is scored exactly like a real eval, not a bespoke check.
Compose:  --report reads the JSONL and prints, per gate, which variant won and by how much, then a
          recommendation: adopt candidate / keep baseline / inconclusive. Needs >= --min-margin (default
          0.34, i.e. roughly 1 run in 3) separating the two pass-rates before recommending adopt -- anything
          narrower is noise at this n. (EVALS.md already burned a rerun on an n=2-3 scoring bug; this
          script does not repeat that mistake by pretending small-n margins are signal.)

Usage:
  harness_probe.py --repo <checkout> --task stock-event \\
      --guide .claude/skills/how-to-add-an-integration-event/SKILL.md \\
      --candidate /tmp/candidate-skill.md --runs 3 --out probe-results.jsonl
  harness_probe.py --report probe-results.jsonl

Cost: identical model to harness_eval2.py -- each run is one `claude -p` session capped by --budget USD.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness_eval as r1       # noqa: E402  (make_variant, sh)
import harness_eval2 as r2      # noqa: E402  (PROMPTS, gates, scan_transcript, diff_text)


def make_probe_variant(repo, guide_rel, candidate_path, tmp):
    """A `full`-harness clone, optionally with ONE guide file overwritten by candidate content."""
    wd, remote, base = r1.make_variant(repo, "full", tmp)
    if candidate_path:
        dest = os.path.join(wd, guide_rel)
        if not os.path.isfile(dest):
            raise FileNotFoundError(f"--guide {guide_rel} does not exist in {repo} (check the path)")
        shutil.copyfile(candidate_path, dest)
        r1.sh(["git", "add", "-A"], wd, check=True)
        r1.sh(["git", "commit", "-q", "-m", "probe: candidate guide wording", "--no-verify"], wd, check=True,
              env={**os.environ, "LEFTHOOK": "0"})
    return wd, remote, base


def run_one(repo, task, guide, candidate_path, variant, budget, model, out_path):
    tmp = tempfile.mkdtemp(prefix="hprobe-")
    t0 = time.time()
    rec = dict(repo=os.path.basename(repo), task=task, guide=guide, variant=variant, ts=int(t0))
    try:
        wd, remote, base = make_probe_variant(repo, guide, candidate_path if variant == "candidate" else None, tmp)
        cmd = ["claude", "-p", r2.PROMPTS[task], "--output-format", "stream-json", "--verbose", "--permission-mode",
               "acceptEdits", "--allowedTools", "Bash,Edit,Write,Read,Glob,Grep", "--max-budget-usd", str(budget)]
        if model:
            cmd += ["--model", model]
        env = {**{k: v for k, v in os.environ.items() if k != "HARNESS_PROTECT_THRESHOLDS"}, "LEFTHOOK": "0"}
        import subprocess
        p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=1800, env=env)
        bad, turns, cost, final = r2.scan_transcript(p.stdout.splitlines())
        finished = final.get("subtype") == "success" and not final.get("is_error")
        g, new_tests = r2.gates(task, wd)
        art = os.path.join(os.path.dirname(os.path.abspath(out_path)) or ".", "probe-artifacts")
        os.makedirs(art, exist_ok=True)
        stem = f"{task}-{variant}-{int(t0)}"
        open(os.path.join(art, stem + ".patch"), "w").write(r2.diff_text(wd))
        open(os.path.join(art, stem + ".jsonl"), "w").write(p.stdout)
        rec.update(cost_usd=cost, turns=turns, forbidden=bad, gates=g, new_tests=new_tests,
                   subtype=final.get("subtype"), said=(final.get("result") or "")[:160].replace("\n", " "))
        if not finished:
            rec.update(ok=None, evidence=f"INCONCLUSIVE (subtype={final.get('subtype')}): {rec['said'][:80]}")
        else:
            rec.update(ok=True, evidence="gates " + f"{sum(g.values())}/{len(g)}")
    except Exception as e:
        rec.update(ok=None, evidence=f"RUNNER ERROR: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rec["secs"] = round(time.time() - t0)
    return rec


def compose(path, min_margin):
    """The Compose step: per-gate pass rate per variant, and an adopt/keep/inconclusive call."""
    with open(path) as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    if not rows:
        print("no rows")
        return
    by_variant = defaultdict(list)
    for r in rows:
        if r.get("ok"):
            by_variant[r["variant"]].append(r)
    base_runs, cand_runs = by_variant.get("baseline", []), by_variant.get("candidate", [])
    gate_names = sorted({k for r in base_runs + cand_runs for k in (r.get("gates") or {})})
    print(f"task={rows[0]['task']}  guide={rows[0]['guide']}")
    print(f"baseline: {len(base_runs)} conclusive runs   candidate: {len(cand_runs)} conclusive runs")
    print(f"{'gate':32}{'baseline':>10}{'candidate':>11}{'margin':>9}  call")
    verdicts = {}
    for g in gate_names:
        b_rate = (sum(1 for r in base_runs if r["gates"].get(g)) / len(base_runs)) if base_runs else 0.0
        c_rate = (sum(1 for r in cand_runs if r["gates"].get(g)) / len(cand_runs)) if cand_runs else 0.0
        margin = c_rate - b_rate
        if abs(margin) < min_margin or not base_runs or not cand_runs:
            call = "inconclusive"
        elif margin > 0:
            call = "ADOPT candidate"
        else:
            call = "KEEP baseline"
        verdicts[g] = call
        print(f"{g:32}{b_rate*100:9.0f}%{c_rate*100:10.0f}%{margin*100:+8.0f}%  {call}")
    adopt = [g for g, v in verdicts.items() if v == "ADOPT candidate"]
    keep = [g for g, v in verdicts.items() if v == "KEEP baseline"]
    inconclusive = [g for g, v in verdicts.items() if v == "inconclusive"]
    print()
    if adopt and not keep:
        print(f"RECOMMENDATION: adopt the candidate wording for {rows[0]['guide']} -- it improved "
              f"{', '.join(adopt)} with no regression, at margin >= {min_margin*100:.0f}%.")
    elif keep and not adopt:
        print("RECOMMENDATION: keep the current wording; the candidate did not beat it on any gate "
              f"at margin >= {min_margin*100:.0f}%.")
    elif adopt and keep:
        print(f"RECOMMENDATION: mixed result -- candidate helps {adopt} but hurts {keep}. Do not ship it "
              "as-is; compose a version that keeps the sentence(s) responsible for the gains in `keep` "
              "while reverting whatever regressed them (read the per-run diffs in probe-artifacts/).")
    else:
        print(f"RECOMMENDATION: inconclusive on every gate ({inconclusive}) at n={len(base_runs)}/{len(cand_runs)} "
              "and margin >= {:.0f}%. More runs needed before touching the guide, OR -- per EVALS.md's own "
              "conclusion -- stop spending on prose and build a sensor instead.".format(min_margin * 100))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo")
    ap.add_argument("--task", choices=list(r2.PROMPTS), help="one of harness_eval2.py's PROMPTS")
    ap.add_argument("--guide", help="path (relative to repo root) of the ONE guide file under probe")
    ap.add_argument("--candidate", help="path to the candidate file content; omit to only run baseline runs")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--budget", type=float, default=6.0)
    ap.add_argument("--model", default="")
    ap.add_argument("--out", default="probe-results.jsonl")
    ap.add_argument("--report")
    ap.add_argument("--min-margin", type=float, default=0.34)
    a = ap.parse_args()
    if a.report:
        return compose(a.report, a.min_margin)
    if not (a.repo and a.task and a.guide):
        ap.error("--repo, --task and --guide are required unless --report is given")
    repo = os.path.abspath(os.path.expanduser(a.repo))
    variants = ["baseline", "candidate"] if a.candidate else ["baseline"]
    total = 0.0
    for i in range(a.runs):
        for variant in variants:
            rec = run_one(repo, a.task, a.guide, a.candidate, variant, a.budget, a.model, a.out)
            total += rec.get("cost_usd") or 0
            open(a.out, "a").write(json.dumps(rec) + "\n")
            print(f"run{i + 1} {variant:9} ok={rec['ok']} turns={rec.get('turns')} "
                  f"${rec.get('cost_usd') or 0:.2f} total=${total:.2f} {rec['evidence'][:70]}", flush=True)


if __name__ == "__main__":
    main()
