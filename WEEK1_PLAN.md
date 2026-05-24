# Week 1 Development Plan

## Goal

Build the first working version of the e-mail schedule extraction module.

By the end of week 1, the system should accept an e-mail thread and return structured JSON containing:

- intent
- participants
- candidate times
- unavailable times
- location preference
- missing information
- extraction confidence

## Team Split

### A. Agent Core

- Define Python project structure.
- Implement extraction schema.
- Add rule-based fallback extractor.
- Prepare Gemini API wrapper later.
- Keep the command-line demo runnable.

### B. Prompt & Evaluation

- Improve `prompts/constraint_extraction_prompt.md`.
- Create 10-20 e-mail samples.
- Write matching `.gold.json` labels.
- Compare prompt versions using the evaluator.
- Record frequent extraction errors.

## Suggested Weekly Checklist

- Day 1: Project setup and schema design.
- Day 2: Constraint extraction prompt v1.
- Day 3: Sample e-mail and gold label format.
- Day 4: Rule-based fallback and Gemini prompt test.
- Day 5: Extraction evaluation and error analysis.
- Day 6-7: Refine schema, prompt, and examples.

## Commands

Run extraction:

```powershell
python -m src.email_agent.extract --sample data\samples\email_001.txt
```

Run LangGraph + Gemini extraction:

```powershell
python -m src.email_agent.run_graph --sample data\samples\email_001.txt --reference-date 2026-05-23
```

Run evaluation:

```powershell
python -m src.email_agent.evaluate --sample data\samples\email_001.txt --gold data\samples\email_001.gold.json
```

Run tests:

```powershell
python -m unittest discover -s tests
```
