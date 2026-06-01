# E-mail-to-Action Agent

이 프로젝트는 이메일 대화에서 일정 조율 정보를 추출하고, 이후 캘린더 확인, 시간 추천, 장소 선택, GUI 예약, 답장 생성까지 확장하는 것을 목표로 하는 LangGraph 기반 Agentic AI 시스템입니다.

현재 구현 범위는 1주차 MVP에 해당하는 **이메일 기반 일정 정보 추출 모듈**입니다. Gemini API 기반 LLM 추출기를 기본으로 설계하고, API 키가 없거나 LLM 호출에 실패할 경우 rule-based baseline으로 fallback되도록 구성했습니다.

## 프로젝트 목표

사용자가 이메일 thread를 입력하면 시스템이 다음 정보를 구조화된 JSON으로 추출합니다.

- 일정 조율 의도
- 참석자
- 가능한 시간 후보
- 불가능한 시간
- 장소 선호
- 회의 시간
- 추가로 필요한 정보
- 추출 신뢰도

최종 목표 workflow는 다음과 같습니다.

```text
Email Input
-> Constraint Extraction
-> Calendar Conflict Check
-> Time Optimization
-> Reply Draft Generation
-> Place Decision
-> GUI Reservation
-> Reflection / Replanning
-> E-mail Reply Generation
```

## 현재 구현된 기능

- 이메일 thread 파싱
- Gemini API 기반 일정 정보 추출 설계
- `.env` 기반 API 키 로드
- LangGraph 기반 extraction workflow
- Pydantic schema validation
- rule-based fallback extractor
- 샘플 이메일 및 gold label
- 간단한 evaluation script
- unittest 기반 테스트

## 프로젝트 구조

```text
agentic/
├─ data/
│  ├─ benchmarks/
│  │  └─ week1_event_extraction.jsonl
│  ├─ calendars/
│  │  └─ synthetic_calendar_001.json
│  └─ samples/
│     ├─ extraction_001.normalized.json
│     ├─ email_001.txt
│     └─ email_001.gold.json
├─ prompts/
│  └─ constraint_extraction_prompt.md
├─ src/
│  └─ email_agent/
│     ├─ config.py
│     ├─ extractor.py
│     ├─ llm_extractor.py
│     ├─ graph.py
│     ├─ run_graph.py
│     ├─ scheduling.py
│     ├─ evaluate.py
│     └─ schema.py
├─ tests/
├─ BENCHMARKS.md
├─ requirements.txt
└─ WEEK1_PLAN.md
```

## 설치

Conda 환경을 사용하는 경우:

```bat
cd /d C:\VSProject\agentic

conda create -n agentic python=3.11 -y
conda activate agentic

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Jupyter kernel을 등록하려면:

```bat
pip install ipykernel jupyter
python -m ipykernel install --user --name agentic --display-name "Python (agentic)"
```

## 환경 변수 설정

Gemini API를 사용하려면 `.env.example`을 복사해 `.env` 파일을 만든 뒤 API 키를 입력합니다.

```bat
copy .env.example .env
notepad .env
```

`.env` 예시:

```text
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
```

`.env` 파일은 `.gitignore`에 포함되어 있으므로 GitHub에 업로드되지 않습니다.

## 실행 방법

Rule-based baseline extractor 실행:

```bat
python -m src.email_agent.extract --sample data\samples\email_001.txt
```

LangGraph + Gemini 기반 extraction workflow 실행:

```bat
python -m src.email_agent.run_graph --sample data\samples\email_001.txt --reference-date 2026-05-23
```

Gemini API 키가 없거나 LLM 호출이 실패하면 자동으로 rule-based fallback이 실행됩니다.

Calendar 파일까지 연결해 scheduling node를 함께 실행할 수도 있습니다.

```bat
python -m src.email_agent.run_graph --sample data\samples\email_001.txt --reference-date 2026-05-23 --calendar data\calendars\synthetic_calendar_001.json
```

정적 HTML 검색 결과를 BeautifulSoup provider로 파싱해 장소 후보까지 연결할 수 있습니다.

```bat
python -m src.email_agent.run_graph --sample data\samples\email_001.txt --reference-date 2026-05-23 --calendar data\calendars\synthetic_calendar_001.json --place-provider html --place-search-html data\place_search\soongsil_cafes.html
```

Kakao Local API 키가 `.env`에 있으면 실제 장소 검색 provider도 사용할 수 있습니다.

```bat
python -m src.email_agent.run_graph --sample data\samples\email_001.txt --reference-date 2026-05-23 --calendar data\calendars\synthetic_calendar_001.json --place-provider kakao
```

Graph workflow는 extraction 이후 `normalize_times` node를 통해 자연어 시간 표현을 ISO datetime으로 변환한 뒤 calendar judge와 optimizer를 실행합니다.

Time normalizer만 단독으로 실행할 수도 있습니다.

```bat
python -m src.email_agent.time_normalizer --extraction data\samples\extraction_001.normalized.json --reference-date 2026-05-27
```

Place retriever만 단독으로 실행할 수도 있습니다.

```bat
python -m src.email_agent.place_retriever --extraction data\samples\email_001.gold.json --provider html --html data\place_search\soongsil_cafes.html
```

Kakao provider 단독 실행:

```bat
python -m src.email_agent.place_retriever --extraction data\samples\email_001.gold.json --provider kakao
```

평가 실행:

```bat
python -m src.email_agent.evaluate --sample data\samples\email_001.txt --gold data\samples\email_001.gold.json
```

1주차 event extraction benchmark 실행:

```bat
python -m src.email_agent.benchmark --benchmark data\benchmarks\week1_event_extraction.jsonl
```

Gemini API로 작은 benchmark subset을 직접 평가:

```bat
python -m src.email_agent.llm_benchmark --benchmark data\benchmarks\week1_event_extraction.jsonl --limit 10 --reference-date 2026-05-26 --output reports\gemini_week1_eval.json
```

로컬 MailEx 원본 데이터셋을 직접 사용해 Gemini 평가:

```bat
python -m src.email_agent.llm_benchmark --mailex-root dataset\data --split test --limit 10 --reference-date 2026-05-26 --output reports\gemini_mailex_test_10_eval.json
```

KVRET calendar scheduling subset을 직접 사용해 Gemini 평가:

```bat
python -m src.email_agent.llm_benchmark --kvret-root dataset\kvret --split test --limit 10 --reference-date 2026-05-27 --output reports\gemini_kvret_test_10_eval.json
```

KVRET만 반복 평가할 때는 아래 파일의 상단 설정값을 바꾼 뒤 실행합니다.

```bat
python scripts\run_kvret_eval.py
```

2주차 calendar judge + time optimizer 실행:

```bat
python -m src.email_agent.scheduling --extraction data\samples\extraction_001.normalized.json --calendar data\calendars\synthetic_calendar_001.json
```

테스트 실행:

```bat
python -m unittest discover -s tests
```

## 출력 예시

```json
{
  "intent": "schedule_meeting",
  "participants": ["동욱", "정인", "민수"],
  "candidate_times": [
    {
      "participant": "동욱",
      "expression": "화요일 오후 3시 이후",
      "normalized_start": null,
      "normalized_end": null,
      "availability": "available"
    }
  ],
  "unavailable_times": [],
  "location_preference": "숭실대 근처 카페",
  "meeting_duration_minutes": null,
  "missing_information": [],
  "confidence": 0.8,
  "source_summary": "팀플 회의를 위해 가능한 시간을 조율하고 있다."
}
```

## 평가 데이터셋 링크

이 프로젝트에서는 실제 개인정보가 포함된 이메일/캘린더 데이터를 직접 사용하기 어렵기 때문에, 공개 데이터셋과 synthetic dataset을 조합해 평가하는 방식을 사용합니다.

| 평가 영역 | 데이터셋 | 용도 | 링크 |
|---|---|---|---|
| 이메일 이벤트 추출 | MailEx | 이메일 thread에서 event/action/argument 추출 평가 | [GitHub](https://github.com/salokr/Email-Event-Extraction), [Paper](https://arxiv.org/abs/2305.13469) |
| 실제 이메일 corpus | Enron Email Dataset | realistic e-mail text 기반 robustness test | [CMU Enron](https://www.cs.cmu.edu/~enron/), [EnronData](https://enrondata.org/en/latest/data/edo-enron-email-pst-dataset/) |
| 일정/캘린더 대화 | SMCalFlow | 자연어 일정 요청을 구조화된 calendar action으로 변환하는 평가 | [Project Page](https://microsoft.github.io/task_oriented_dialogue_as_dataflow_synthesis/), [Paper](https://arxiv.org/abs/2009.11423) |
| Slot extraction / dialogue state | Schema-Guided Dialogue | intent routing 및 slot extraction 보조 평가 | [GitHub](https://github.com/google-research-datasets/dstc8-schema-guided-dialogue), [Paper](https://arxiv.org/abs/1909.05855) |
| 시간 표현 추출 | TimeBank / TempEval-3 | "다음 주 수요일 오후" 같은 temporal expression 평가 | [TimeBank](https://timeml.github.io/site/timebank/documentation-1.2.html), [TempEval-3](https://figshare.com/articles/dataset/TempEval-3_data/9586532) |
| 웹/GUI 조작 | MiniWoB++ | form filling, clicking 등 web interaction 평가 | [GitHub](https://github.com/Farama-Foundation/miniwob-plusplus), [Docs](https://miniwob.farama.org/) |
| 실제 웹 에이전트 | Mind2Web | real-world website 기반 web agent 평가 | [Project Page](https://osu-nlp-group.github.io/Mind2Web/), [Paper](https://arxiv.org/abs/2306.06070) |
| 데스크톱/웹 GUI Agent | OSWorld | open-ended computer task benchmark | [Paper](https://arxiv.org/abs/2404.07972), [Project](https://os-world.github.io/) |
| End-to-End Agent | AgentBench | LLM-as-Agent benchmark | [GitHub](https://github.com/THUDM/AgentBench), [Paper](https://arxiv.org/abs/2308.03688) |
| Web Agent | WebArena | 장기 웹 task 수행 평가 | [Paper](https://arxiv.org/abs/2307.13854), [GitHub](https://github.com/web-arena-x/webarena) |

## 권장 평가 구성

3~4주 프로젝트 기준으로는 다음 구성이 가장 현실적입니다.

```text
Constraint Extraction:
  MailEx + 직접 제작한 이메일 일정 조율 샘플

Calendar Reasoning:
  Synthetic Calendar Dataset

GUI Execution:
  Mock Reservation Website + MiniWoB++ 참고

End-to-End Evaluation:
  성공, 시간 충돌, 예약 실패, 정보 부족을 포함한 10~20개 시나리오
```

## 개발 로드맵

```text
1주차:
  이메일 일정 정보 추출
  Gemini API 연동
  LangGraph extraction workflow

2주차:
  synthetic calendar 생성
  캘린더 충돌 판단
  최적 시간 추천

3주차:
  장소 추천
  mock reservation page
  GUI 예약 자동화

4주차:
  reflection / replanning
  end-to-end demo
  평가 및 발표 자료 정리
```

## 주의사항

- 실제 이메일과 캘린더 데이터에는 개인정보가 포함될 수 있으므로 공개 저장소에는 업로드하지 않습니다.
- `.env` 파일과 API 키는 GitHub에 올리지 않습니다.
- 실제 예약 사이트는 로그인, CAPTCHA, 약관 문제로 인해 MVP에서는 mock reservation website 사용을 권장합니다.
