# RTST: Real-Time Subtitle Translator

화면에 표시되는 영어 자막 영역을 캡처하고 OCR로 읽은 뒤, 한국어 번역을 투명 오버레이로 띄우는 Python 데스크톱 앱입니다.

## 지원 모드

- `codex_oauth`: OpenClaw 방식을 참고한 ChatGPT/Codex OAuth 로그인 후 `chatgpt.com/backend-api/codex` Responses 백엔드 호출
- `openai`: `OPENAI_API_KEY`로 OpenAI Platform Responses API 직접 호출
- `oauth_proxy`: 로컬 OAuth 프록시에 로그인하고 프록시가 OpenAI Platform API 키로 번역
- `mock`: API 없이 OCR/오버레이 흐름 확인

`codex_oauth`는 일반 OpenAI Platform API OAuth가 아니라 ChatGPT/Codex 로그인 기반 경로입니다. 공식 Platform API 키 방식과 분리된 실험적 호환 모드로 두었습니다.

## 설치

```powershell
cd C:\RTST
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

기본 OCR은 Windows 내장 OCR입니다. Tesseract를 쓰려면 별도로 설치하고 `.env`에 `TESSERACT_CMD`를 지정하세요.

## 한 번에 실행

```powershell
.\run_rtst_oauth.bat
```

배치 파일은 가상환경과 의존성을 확인한 뒤 앱을 `codex_oauth` 모드로 열고 브라우저 로그인을 시작합니다. 이 모드는 `OPENAI_API_KEY`가 없어도 실행됩니다.

캡처 차단 서비스처럼 화면 OCR을 쓸 수 없는 경우에는 DOM 자막 모드를 사용합니다.

```powershell
.\run_rtst_browser_dom.bat
```

이 배치 파일은 Chrome을 `--remote-debugging-port=9222`로 별도 프로필에서 열고, 앱의 `Subtitle source`를 `browser_dom`으로 맞춥니다. 열린 Chrome에서 영상 서비스에 로그인하고 자막을 켠 뒤 앱에서 `Start`를 누르세요. 자막 CSS selector를 알고 있다면 `Subtitle CSS selector`에 넣고, 모르면 비워두면 기본 후보와 `video.textTracks.activeCues`를 읽습니다. YouTube가 다른 웹 페이지 안의 iframe으로 열려 있어도 Chrome frame tree를 따라가며 하위 frame에서 자막을 찾습니다. `Browser tab filter`가 비어 있으면 비디오, 자막, 미디어 iframe이 있는 탭을 우선 선택합니다.
`browser_dom` 모드는 실제 `video.textTracks.activeCues`가 있으면 그 값을 우선 사용하고, DOM 후보를 읽을 때는 영상 상단 타이틀/컨트롤바/메뉴처럼 마우스 오버 때 나타나는 UI 텍스트를 제외합니다. 자식 자막 노드가 따로 잡히는 경우에는 부모 자막 컨테이너를 건너뛰어 같은 문장이 중복으로 번역되는 일을 줄입니다. DOM 텍스트에 플레이어 진행 시간(`0:02 / 19:45`)이나 같은 자막 문구의 통째 반복이 섞이면 후처리에서 제거합니다.

## Codex OAuth 설정

기본값은 `.env.example`에 들어 있습니다.

```env
OPENAI_MODEL=gpt-5-mini
RTST_CODEX_BASE_URL=https://chatgpt.com/backend-api
RTST_CODEX_MODEL=gpt-5.5
RTST_CODEX_REASONING_EFFORT=low
RTST_CODEX_REASONING_SUMMARY=auto
RTST_CODEX_OAUTH_AUTH_URL=https://auth.openai.com/oauth/authorize
RTST_CODEX_OAUTH_TOKEN_URL=https://auth.openai.com/oauth/token
RTST_CODEX_OAUTH_CLIENT_ID=app_EMoamEEZ73f0CkXaXp7hrann
RTST_CODEX_OAUTH_SCOPE=openid profile email offline_access
RTST_CODEX_OAUTH_CALLBACK_HOST=localhost
RTST_CODEX_OAUTH_CALLBACK_PORT=1455
RTST_CODEX_OAUTH_CALLBACK_PATH=/auth/callback
RTST_CODEX_ORIGINATOR=openclaw
RTST_SCAN_INTERVAL_MS=120
RTST_SUBTITLE_STABLE_MS=220
RTST_SUBTITLE_MAX_WAIT_MS=850
RTST_VISUAL_CHANGE_THRESHOLD=1.5
RTST_SAVE_LAST_CAPTURE=1
RTST_LAST_CAPTURE_PATH=rtst_last_capture.png
RTST_DOM_POLL_INTERVAL_MS=250
RTST_BROWSER_DEBUG_URL=http://127.0.0.1:9222
RTST_BROWSER_TAB_FILTER=
RTST_BROWSER_SUBTITLE_SELECTOR=
```

로그인 토큰은 `rtst_codex_oauth_token.json`에 저장됩니다. 문제가 있으면 앱에서 `Clear OAuth token`을 누르거나 해당 파일을 삭제한 뒤 다시 로그인하세요.

`RTST_CODEX_MAX_OUTPUT_TOKENS`는 기본으로 보내지 않습니다. 일부 Codex 백엔드 모델이 이 파라미터를 거부하기 때문입니다.
`RTST_CODEX_REASONING_SUMMARY`는 `auto`, `concise`, `detailed` 중 하나여야 합니다.

## 로그 확인

앱은 `rtst_debug.log`에 캡처/OCR/번역 소요 시간과 OCR/번역 텍스트 일부를 기록합니다. 번역이 이상하면 먼저 이 파일에서 `frame_ocr`, `ocr_result`, `ocr_empty`, `codex_translation_done`, `frame_translated` 줄을 확인하세요.

자막 영역은 자동 변화 감지 방식으로 스캔합니다. UI의 수동 캡처 인터벌 설정은 제거했고, 화면 변화가 감지된 뒤 짧게 안정되면 OCR과 번역을 실행합니다. 고급 조정이 필요하면 `.env`에서 `RTST_SCAN_INTERVAL_MS`, `RTST_SUBTITLE_STABLE_MS`, `RTST_SUBTITLE_MAX_WAIT_MS`, `RTST_VISUAL_CHANGE_THRESHOLD`를 바꿀 수 있습니다.

OCR이 빈 값이면 마지막 OCR 입력 이미지가 기본적으로 `rtst_last_capture.png`에 저장됩니다. 이 이미지가 검거나 자막을 포함하지 않으면 `Select subtitle region`으로 영역을 다시 잡아야 합니다.

오버레이는 최근 번역 히스토리를 작은 채팅 패널처럼 보여줍니다. 화면 위에서 직접 드래그해 옮길 수 있고, 놓은 위치는 `manual` 위치로 저장됩니다. 오른쪽 아래 모서리를 드래그하면 오버레이 크기를 바로 조절할 수 있으며, `Overlay width`와 `Overlay height` 값에도 반영됩니다. `Overlay history`로 오버레이에 보일 최근 번역 개수를 조절합니다.
이전 번역들은 `History` 탭에도 원문/번역 쌍으로 계속 쌓이며, `Max`로 전체 보관 개수를 조절하고 `Clear history`로 비울 수 있습니다.

## 직접 API 키 모드

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5-mini
```

```powershell
.\.venv\Scripts\python.exe main.py
```

앱에서 `Translator`를 `openai`로 선택하고 API 키를 입력하거나 `.env`에 둔 뒤 시작합니다.

## 사용 순서

1. 앱을 실행합니다.
2. `Run` 탭에서 `Subtitle source`, `Translator`, `Target language`를 선택합니다.
3. OAuth 모드라면 상단 `OAuth login`으로 브라우저 로그인을 완료합니다.
4. `screen_ocr` 모드라면 `Select subtitle region`으로 자막 영역을 드래그합니다.
5. 이전 번역은 `History` 탭에서 확인합니다.
6. 세부 설정은 `Source`, `Translation`, `Overlay`, `Advanced` 탭에서 조정합니다.
7. `Start`를 누르면 자막 읽기와 번역 오버레이가 시작됩니다.

## 테스트

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall main.py rtst_app server tests
```

자세한 사양은 [SPEC.md](C:\RTST\SPEC.md)를 참고하세요.
