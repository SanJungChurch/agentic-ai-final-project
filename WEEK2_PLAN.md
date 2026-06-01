# Week 2 Development Plan

## Goal

이메일에서 추출된 후보 시간 중 캘린더 충돌이 없고 참석자 선호를 가장 잘 만족하는 시간을 추천합니다.

## Optimizer Policy

MVP에서는 복잡한 수리최적화 대신 설명 가능한 heuristic scoring 방식을 사용합니다.

```text
candidate time
-> hard constraint filtering
-> soft preference scoring
-> best candidate selection
```

## Hard Constraints

하나라도 위반하면 후보에서 제외합니다.

- calendar conflict
- outside allowed hours
- invalid time range
- explicit unavailable time

## Soft Score

hard constraint를 통과한 후보에 대해 점수를 계산합니다.

- available participant: +10 per person
- common time with at least two participants: +40
- preferred participant: +20 per person
- reasonable daytime slot, 10:00-18:59: +10
- sufficient duration, at least 60 minutes: +5
- too early or too late: -10

## Run

```bat
python -m src.email_agent.scheduling --extraction data\samples\extraction_001.normalized.json --calendar data\calendars\synthetic_calendar_001.json
```

The LangGraph workflow also generates a user-facing reply draft after scheduling:

```text
Email Input
-> Constraint Extractor
-> Time Normalizer
-> Calendar Judge / Time Optimizer
-> Place Retriever / Place Decision
-> Reply Generator
```

Place retrieval uses a provider interface:

```text
PlaceProvider
-> MockPlaceProvider
-> StaticHtmlPlaceProvider using BeautifulSoup
-> KakaoLocalPlaceProvider using Kakao Local API
```

## Current Limitation

The time normalizer currently supports common Korean and English expressions only.

For now:

- If `normalized_start` and `normalized_end` already exist, keep them.
- Otherwise, normalize common expressions such as `수요일 5시`, `다음 주 월요일 오후 2시`, `Friday 11 am`, and `tomorrow 7pm`.
- If normalization fails, leave the original expression unresolved for later user clarification.
