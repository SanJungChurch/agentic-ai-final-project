# Streamlit Demo

시연 영상용 화면은 Streamlit으로 실행하고, ShowUI가 조작하는 대상은 별도의 HTML 예약 페이지로 둔다.

```text
Streamlit dashboard
-> LangGraph pipeline 실행
-> ShowUI runner 호출
-> data/reservation/mock_reservation.html 조작
-> 예약 결과와 답장 초안 표시
```

## 실행

```bat
streamlit run streamlit_app.py
```

## 권장 시연 순서

1. Streamlit 화면에서 입력 이메일을 보여준다.
2. `Reservation executor`를 선택한다.
   - `DOM fallback demo`: 빠르고 안정적인 녹화용
   - `Local GPU ShowUI-2B`: 실제 ShowUI grounding 시연용
3. `Run Pipeline`을 누른다.
4. 오른쪽 탭에서 extraction, schedule, place, reservation, reply 결과를 순서대로 보여준다.

## ShowUI 조작 대상

```text
data/reservation/mock_reservation.html
```

이 HTML 페이지는 실제 카페 예약 페이지처럼 구성한 mock booking page다. ShowUI/Playwright가 실제로 입력창을 클릭하고 값을 입력한 뒤 `Reserve` 버튼을 누르는 대상이다.

## Place Retrieval / Booking 분리

시연 구조에서는 지도 서비스와 GUI 예약 페이지의 역할을 분리한다.

```text
Kakao Local REST API / static place search HTML
-> 장소 후보 retrieve
-> 예약 불가능 후보 제거
-> 예약 가능한 후보 선택
-> ShowUI가 booking page 조작
```

Kakao 지도나 Naver 지도 화면은 검색 결과와 상세 패널이 자주 바뀌므로 ShowUI 예약 실행 대상보다는 upstream retrieval 도구로 사용한다. ShowUI는 예약 폼이 있는 booking page를 downstream action target으로 조작한다.
