# HTML 시연 영상 가이드

Streamlit 없이 단일 HTML 예약 페이지를 띄워 ShowUI 예약 동작을 촬영하는 시연 구성입니다.

## 예약 페이지

- 파일: `demo/recording_reservation_site.html`
- 형태: 실제 예약 사이트처럼 보이는 로컬 HTML mockup
- 선택 장소: `카페 온더힐`
- 예약 가능 슬롯: `2026-05-27 17:00`
- 성공 예약번호: `HTML-20260527-1700`

> 실제 상용 예약 사이트의 HTML을 그대로 복제하지 않고, 수업 시연용으로 동일한 GUI 작업 흐름을 재현하는 독립 HTML 페이지입니다.

## 빠른 검증

```cmd
python scripts\run_html_recording_demo.py --headless
```

## ShowUI 로컬 GPU 검증

```cmd
python scripts\run_html_recording_demo.py --headless --executor local-showui
```

성공 시 `reservation_result.status`가 `confirmed`이고, `steps`에 다음 문구가 포함됩니다.

```json
"fill form using ShowUI grounded clicks",
"submit reservation form using ShowUI grounded click"
```

## 영상 촬영용 실행

브라우저 창을 띄워서 촬영하려면 `--headless`를 빼고 실행합니다.

```cmd
python scripts\run_html_recording_demo.py --executor local-showui
```

안정적인 촬영이 필요하면 DOM fallback 모드도 사용할 수 있습니다.

```cmd
python scripts\run_html_recording_demo.py
```

