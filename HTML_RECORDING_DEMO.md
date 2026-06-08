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

## HTML 입력 데모

Streamlit 없이 이메일 입력부터 실행하려면 로컬 HTML 서버를 실행합니다.

```cmd
python scripts\serve_html_demo.py --port 8765
```

브라우저에서 `http://127.0.0.1:8765/`에 접속합니다.

- `Open target`: 현재 executor와 이메일 장소 표현에 맞는 예약 target을 엽니다.
- `Open mock`: 안정 시연용 local mock 예약 페이지를 엽니다.
- `Place provider`: `Demo dynamic`은 이메일의 장소 표현이 숭실대/강남/홍대/판교로 바뀌면 추천 장소도 바꿉니다.
- `Naver booking URL`: 비워두면 이메일 장소 표현을 기준으로 기본 네이버 검색/예약 URL을 자동 선택합니다.

## Google Workspace 연동

Gmail/Google Calendar 연동은 선택 기능입니다.

1. Google Cloud Console에서 OAuth Client를 `Desktop app`으로 생성합니다.
2. JSON 파일을 `credentials/google_oauth_client.json`에 저장합니다.
3. 필요한 패키지를 설치합니다.

```cmd
pip install -r requirements.txt
```

HTML 화면에서 `Load Gmail`을 누르면 최초 1회 OAuth 브라우저 인증이 실행되고,
토큰은 `credentials/google_workspace_token.json`에 저장됩니다.
