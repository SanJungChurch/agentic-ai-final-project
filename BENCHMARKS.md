# Week 1 Benchmark

1주차의 평가 목적은 **이메일 또는 대화에서 일정 관련 event와 slot을 구조화해서 추출할 수 있는지** 확인하는 것입니다.

## Primary Benchmark: MailEx

- Name: MailEx: Email Event and Argument Extraction
- Task: e-mail thread에서 event와 argument 추출
- Why: 본 프로젝트의 입력 형태가 e-mail thread이므로 가장 직접적으로 대응됩니다.
- Links:
  - GitHub: https://github.com/salokr/Email-Event-Extraction
  - Paper: https://arxiv.org/abs/2305.13469

## Auxiliary Benchmark: SMCalFlow

- Name: SMCalFlow
- Task: calendar, people, place 관련 task-oriented dialogue를 structured action으로 변환
- Why: 일정/캘린더 기반 constraint extraction 평가에 적합합니다.
- Links:
  - Project: https://microsoft.github.io/task_oriented_dialogue_as_dataflow_synthesis/
  - Paper: https://arxiv.org/abs/2009.11423

## Local Benchmark Format

원본 MailEx 데이터를 바로 repository에 포함하지 않고, 동일한 평가 목적을 가진 작은 local benchmark를 `JSONL` 형식으로 둡니다.

```json
{
  "id": "week1_email_001",
  "source": "local_mailex_style",
  "text": "Subject: ...",
  "gold": {
    "intent": "schedule_meeting",
    "participants": ["동욱", "정인"],
    "candidate_times": [
      {
        "participant": "동욱",
        "expression": "화요일 오후 3시",
        "availability": "available"
      }
    ],
    "unavailable_times": [],
    "location_preference": "숭실대 근처 카페"
  }
}
```

## Metrics

- Participant Exact Match
- Candidate Time Slot F1
- Unavailable Time Slot F1
- Location Detection Accuracy

## Run

```bat
python -m src.email_agent.benchmark --benchmark data\benchmarks\week1_event_extraction.jsonl
```

현재 benchmark runner는 rule-based extractor를 기본값으로 사용합니다. Gemini 기반 extractor가 안정화되면 같은 JSONL benchmark에 대해 provider를 추가해 prompt version별 성능 비교를 수행합니다.
