# Week 1 KVRET Evaluation

## Dataset Structure

KVRET is stored under:

```text
dataset/kvret/
├─ kvret_train_public.json
├─ kvret_dev_public.json
├─ kvret_test_public.json
├─ kvret_entities.json
└─ kvret_dataset_public.zip
```

The evaluator uses only dialogues whose scenario intent is `schedule`.

## Slot Mapping

| KVRET Slot | Project Schema |
|---|---|
| `party` | `participants` |
| `date` + `time` | `candidate_times[].expression` |
| `room` | `location_preference` |
| `event`, `agenda` | `source_summary` |

## How to Run

Edit the CONFIG section in:

```text
scripts/run_kvret_eval.py
```

Then run:

```bat
python scripts\run_kvret_eval.py
```

You can also run it as a module from the project root:

```bat
python -m scripts.run_kvret_eval
```

Common values to change:

```python
SPLIT = "test"      # "train", "dev", or "test"
LIMIT = 10          # number of examples
OFFSET = 0          # skip N schedule examples
OUTPUT_PATH = Path("reports/gemini_kvret_test_10_eval.json")
```

## Metrics

The report includes:

- API success rate
- JSON valid rate
- participant exact match
- participant F1
- candidate time F1
- unavailable time F1
- location F1
- overall score

The evaluator ignores generic dialogue speakers such as `driver`, `assistant`, and `sender` for participant matching.
