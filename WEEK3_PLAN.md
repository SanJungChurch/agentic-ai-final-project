# Week 3 개발 계획: GUI 예약 수행 MVP

## 목표

Week 3의 목표는 Week 2에서 선택한 시간과 장소를 바탕으로 예약 페이지를 조작하는 실행 단계를 LangGraph workflow에 연결하는 것이다.

```text
Extraction -> Normalization -> Scheduling -> Place Retrieval -> Reservation Execution -> Reply Draft
```

## 현재 구현 범위

- `ReservationRequest`, `ReservationResult` schema 추가
- `MockReservationExecutor` 추가
- `StaticHtmlReservationExecutor` 추가
- `ShowUIReservationExecutor` adapter 추가
- mock reservation HTML 페이지 추가
- LangGraph에 `reserve_place` node 추가
- `run_graph.py`에 reservation provider 옵션 추가
- 답장 생성 단계에서 예약 성공/실패 상태 반영
- reservation executor 단위 테스트 추가

## MVP 실행 방식

실제 예약 사이트는 로그인, CAPTCHA, 약관, 결제, 개인정보 입력 문제가 있을 수 있으므로 MVP에서는 정적 HTML 예약 페이지를 사용한다.

```bat
python -m src.email_agent.run_graph --sample data\samples\email_001.txt --reference-date 2026-05-23 --calendar data\calendars\synthetic_calendar_001.json --place-provider html --place-search-html data\place_search\soongsil_cafes.html --reservation-provider html --reservation-html data\reservation\mock_reservation.html
```

ShowUI executor adapter:

```bat
python -m src.email_agent.run_graph --sample data\samples\email_001.txt --reference-date 2026-05-23 --calendar data\calendars\synthetic_calendar_001.json --place-provider html --place-search-html data\place_search\soongsil_cafes.html --reservation-provider showui --reservation-target file:///C:/VSProject/agentic/data/reservation/mock_reservation.html
```

`SHOWUI_RUNNER_COMMAND`가 설정되어 있으면 외부 ShowUI runner가 task JSON을 실행한다. 아직 runner가 연결되지 않은 경우에는 `needs_manual_action`으로 ShowUI task prompt를 반환한다.

ShowUI runner script:

```bat
set SHOWUI_RUNNER_COMMAND=python scripts\showui_reservation_runner.py --headless --dom-fallback
```

공식 ShowUI repo는 `third_party\ShowUI`에 clone해서 참고한다. 이 폴더는 `.gitignore`에 포함한다.

```bat
mkdir third_party
git clone https://github.com/showlab/ShowUI.git third_party\ShowUI
```

실제 ShowUI Hugging Face Space 또는 로컬 서버를 사용할 때:

```bat
set SHOWUI_GRADIO_SOURCE=showlab/ShowUI
set SHOWUI_RUNNER_COMMAND=python scripts\showui_reservation_runner.py --headless
```

Local GPU ShowUI-2B:

```bat
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install transformers accelerate qwen-vl-utils safetensors
python scripts\run_showui_e2e_demo.py --local-showui
```

End-to-end demo:

```bat
python scripts\run_showui_e2e_demo.py
```

## 다음 확장

- 실제 ShowUI 서버 연결 및 browser action loop 검증
- 예약 실패 시 다른 장소/시간으로 replanning
- reservation success judge 분리
- end-to-end scenario 평가 데이터 작성
