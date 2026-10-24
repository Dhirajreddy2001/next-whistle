# Q&A evaluation

`golden.py` builds about 80 cases from the real dataset (so event IDs never go stale): explanations of goals, red and yellow cards, saves and substitutions at three viewer levels (plus Spanish), referee questions on every recorded decision, and match recaps.

`run_eval.py` runs each case through the agent workflow and scores it:

| Check | Meaning |
| --- | --- |
| `completed` | The workflow produced a validated answer (not the factual fallback) |
| `evidence_cited` | The answer cites evidence that exists and is visible at the viewer's time |
| `level_numbers`, `level_words` | The answer respects the beginner / intermediate / pro limits |
| `ruling_exact` | A referee answer repeats the recorded ruling exactly |
| `rule_cited` | A referee answer cites rule IDs that exist in the pinned rules |
| `uncertainty_honoured` | A call inside measurement uncertainty is reported as `not_settled` |
| `language` | Spanish cases are answered in Spanish |

The summary also reports whether answers really differ across the three levels for the same moment.

```
make eval                                  # scripted roles: no model, no cost
make eval-live MAX_USD=2 LIMIT=12          # real Foundry models: spends money
```

`make eval-live` needs `FOUNDRY_PROJECT_ENDPOINT`, `MODEL_TOOLS`, `MODEL_SMALL`, `PRICE_INPUT_PER_MTOK` and `PRICE_OUTPUT_PER_MTOK`, and a prior `az login`. It refuses to start without a spend cap, prints an estimate first, and stops when the running cost reaches the cap. The scripted run proves the plumbing; only the live run measures model quality. Reports are written to `eval/reports/` (not committed).
