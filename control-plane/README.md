# Control plane

Cross-repository coordination for AdaptEng Company OS.

`registry/` records **what exists**. `runbooks/` record **how to operate it**.
This folder records **what is true right now across all repositories, what is
allowed to happen without the owner, and what work is currently dispatched**.

| File | Purpose |
|---|---|
| [`current-state.md`](current-state.md) | Verified cross-repository state and the drift register: every place documentation disagrees with production or `main`. |
| [`autonomy-policy.md`](autonomy-policy.md) | The owner-versus-agent permission model. What an agent may do alone, what it may do if automated verification passes, and the short list that is genuinely owner-only. |
| [`friction-audit.md`](friction-audit.md) | Every material protection mechanism classified P0–P3, with the exact remediation for the ones that cost more than they protect. |
| [`execution-program.md`](execution-program.md) | Current workstreams, their dependency order, and the dispatch package for each. |

## How to use this folder

Read `current-state.md` first. It is the only file here that makes claims about
reality, and every claim carries the evidence that supports it.

The other three are decisions, not observations. They change only by pull
request.

## Evidence rule

A claim in this folder must name its source. Sources rank in this order:

```
production runtime
  > current main
    > merged PR / CI evidence
      > repository code
        > registry or runbook
          > prior narrative
```

Where a lower source contradicts a higher one, the higher source wins and the
disagreement is recorded in the drift register rather than silently corrected.

Claims that could not be checked from this workstation are marked
`UNVERIFIED` and say what would settle them. An unverified claim is not a
blocker; an unverified claim treated as fact is.
