# RTST 프로그램 사양서

## 1. 개요

RTST는 영어 공부를 위해 화면에 보이는 영어 자막을 실시간에 가깝게 OCR로 인식하고, 번역 결과를 별도 투명 오버레이로 표시하는 Windows 데스크톱 프로그램이다.

기본 `screen_ocr` 모드는 모든 플레이어의 자막 데이터를 직접 가져오지 않고, 사용자가 지정한 화면 영역만 자동 스캔한다. 캡처가 차단되지만 페이지 DOM에 자막 텍스트가 노출되는 서비스에서는 `browser_dom` 모드로 Chrome DevTools Protocol을 통해 현재 페이지의 자막 텍스트를 읽는다.

## 2. 번역 제공자

- `codex_oauth`: OpenClaw 구현을 참고한 ChatGPT/Codex OAuth 로그인 방식이다. `auth.openai.com`에서 Authorization Code + PKCE로 토큰을 받고, `chatgpt.com/backend-api/codex/responses`를 호출한다.
- `openai`: 데스크톱 앱이 `OPENAI_API_KEY`로 OpenAI Platform Responses API를 직접 호출한다.
- `oauth_proxy`: 데스크톱 앱이 로컬 OAuth 프록시에 로그인하고, 프록시가 서버 측 `OPENAI_API_KEY`로 OpenAI Platform Responses API를 호출한다.
- `mock`: 개발 테스트용으로 입력 문장을 그대로 표시한다.

`codex_oauth`는 일반 OpenAI Platform API OAuth가 아니다. ChatGPT/Codex 계정 로그인과 Codex 백엔드에 묶인 실험적 호환 모드이며, 실패하거나 계정/모델 권한에 따라 동작하지 않을 수 있다.

## 3. Codex OAuth 흐름

```text
Desktop app
  -> http://localhost:1455/auth/callback 임시 콜백 서버
  -> https://auth.openai.com/oauth/authorize
  -> authorization code 수신
  -> https://auth.openai.com/oauth/token
  -> access/refresh token 저장
  -> https://chatgpt.com/backend-api/codex/responses
  -> SSE 응답에서 번역 텍스트 추출
```

기본 OAuth 파라미터:

- `client_id`: `app_EMoamEEZ73f0CkXaXp7hrann`
- `scope`: `openid profile email offline_access`
- `redirect_uri`: `http://localhost:1455/auth/callback`
- 추가 authorize 파라미터: `id_token_add_organizations=true`, `codex_cli_simplified_flow=true`, `originator=openclaw`

Codex 백엔드 호출 헤더:

- `Authorization: Bearer <access_token>`
- `chatgpt-account-id: <JWT claim에서 추출한 계정 id>`
- `OpenAI-Beta: responses=experimental`
- `originator: openclaw`
- `Accept: text/event-stream`

## 4. 주요 기능

- 전체 화면 위에 영역 선택 레이어를 띄워 자막 영역을 지정한다.
- UI는 `Run`, `History`, `Source`, `Translation`, `Overlay`, `Advanced` 탭으로 나누고 실행에 필요한 핵심 옵션만 첫 탭에 둔다.
- `mss`로 지정 영역을 빠르게 스캔하고, 화면 변화가 안정되거나 최대 대기 시간을 넘겼을 때만 OCR을 실행한다.
- `browser_dom` 모드에서는 Chrome DevTools 포트에 연결해 `video.textTracks.activeCues`, 흔한 자막 selector, shadow DOM 내부 selector에서 자막 텍스트를 읽는다.
- `browser_dom` 모드는 실제 `textTracks.activeCues`를 DOM selector보다 우선하고, DOM fallback에서는 마우스 오버 시 나타나는 플레이어 타이틀/컨트롤/메뉴 텍스트와 자식 자막 노드를 가진 부모 컨테이너를 제외한다.
- `browser_dom` 모드는 DOM fallback 결과에서 플레이어 진행 시간과 완전히 반복된 자막 문구를 후처리로 줄인다.
- `browser_dom` 모드는 탭 필터가 비어 있으면 Chrome DevTools의 debuggable page 중 비디오, 자막 DOM, 미디어 iframe이 있는 탭을 우선 선택한다.
- `browser_dom` 모드는 Chrome frame tree를 순회하고 하위 frame마다 isolated world를 만들어 embed/iframe 플레이어 자막도 시도한다.
- Windows OCR 또는 Tesseract OCR을 선택할 수 있다.
- OCR 결과를 정규화하고 직전 문장과 비교해 중복 번역 호출을 줄인다.
- 최근 번역 히스토리를 화면 위 투명 오버레이에 채팅 패널 형태로 표시한다.
- 오버레이 표시 여부는 UI에서 켜고 끌 수 있다.
- 오버레이 히스토리 패널은 최신 항목으로 자동 스크롤되며, 사용자가 패널 안에서 스크롤해 이전 번역을 확인할 수 있다.
- 원문 표시 여부, 오버레이 글자 크기, 투명도를 UI에서 조정한다.
- 오버레이 위치는 자동, 하단, 상단, 중앙, 사용자 지정 영역, 수동 드래그 위치와 X/Y 오프셋으로 조정한다.
- 오버레이 폭과 높이를 UI에서 조정하거나 오버레이 오른쪽 아래 모서리를 직접 드래그해 조정한다.
- 오버레이를 직접 드래그하면 수동 위치로 저장하고 이후 번역도 해당 위치에 표시한다.
- 오버레이에 보일 최근 번역 항목 수는 UI에서 조절한다.
- 전체 번역 내역은 `History` 탭에 채팅 로그 형태로 표시하고, 최대 보관 개수를 조절하거나 수동으로 비울 수 있다.
- OAuth access token이 만료되면 refresh token으로 갱신을 시도한다.

## 5. 설정

기본 설정 파일은 프로젝트 루트의 `rtst_settings.json`이다.

`.env` 주요 항목:

```env
OPENAI_API_KEY=
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

보안상 API 키와 OAuth 토큰 파일은 `.gitignore`에 포함한다.

디버그 로그는 기본적으로 `rtst_debug.log`에 기록한다. 로그에는 OCR 텍스트와 번역 텍스트 일부가 포함되므로 공유 전 내용을 확인해야 한다.
OCR 입력 진단을 위해 마지막 OCR 대상 이미지를 `rtst_last_capture.png`에 저장한다. `RTST_SAVE_LAST_CAPTURE=0`으로 비활성화할 수 있다.

자막 변화 감지는 UI 설정이 아니라 내부 자동 동작이다. 기본 스캔 주기는 120ms이며, OCR은 매 스캔마다 실행하지 않고 `RTST_SUBTITLE_STABLE_MS` 또는 `RTST_SUBTITLE_MAX_WAIT_MS` 조건을 만족하는 최신 프레임에 대해서만 실행한다.

`max_output_tokens`는 Codex OAuth 기본 요청에서 제외한다. 일부 ChatGPT/Codex 백엔드 모델이 해당 파라미터를 거부하기 때문이다.
`reasoning.effort`는 기본 `low`를 사용하며, 이전 설정값인 `minimal`은 `low`로 자동 매핑한다.
`reasoning.summary`는 Codex 백엔드 허용값인 `auto`, `concise`, `detailed`만 사용한다.

## 6. 아키텍처

```text
main.py
  -> rtst_app.app
    -> MainWindow
    -> RegionSelector
    -> OverlayWindow
    -> FrameProcessor
    -> DomSubtitleProcessor

rtst_app.capture
  -> 화면 영역 캡처

rtst_app.ocr
  -> Windows OCR / Tesseract OCR

rtst_app.browser_dom
  -> Chrome DevTools Protocol 연결
  -> DOM/textTrack 자막 텍스트 읽기
  -> iframe frame tree 순회

rtst_app.oauth_client
  -> OAuth Authorization Code + PKCE
  -> 루프백 callback
  -> token 저장/갱신

rtst_app.codex_oauth
  -> OpenClaw식 Codex OAuth 기본값
  -> ChatGPT account id JWT claim 추출

rtst_app.translator
  -> OpenAI Platform 직접 번역
  -> Codex OAuth 번역
  -> OAuth proxy 번역
  -> Mock 번역

server.oauth_proxy
  -> 로컬 데모 OAuth 프록시
```

## 7. 오류 처리

- OCR 엔진이 준비되지 않으면 시작 전에 경고한다.
- `browser_dom` 모드에서 Chrome DevTools 포트에 연결할 수 없거나 탭을 찾지 못하면 시작 전에 경고한다.
- OAuth 로그인 실패, 콜백 타임아웃, 토큰 갱신 실패를 UI 상태와 경고창으로 표시한다.
- Codex OAuth 토큰에 `chatgpt-account-id` claim이 없으면 시작을 중단한다.
- 번역 요청 오류는 상태 영역에 표시하고 다음 자동 감지 주기에서 재시도한다.
- 빈 OCR 결과와 거의 동일한 OCR 결과는 번역하지 않는다.
- 빈 OCR 결과는 상태 영역에 명확히 표시하고 마지막 OCR 입력 이미지를 남긴다.

## 8. 제한 사항

- 화면 OCR 기반이라 자막 배경, 폰트, 해상도에 따라 인식률이 달라진다.
- DRM이나 캡처 차단 화면에서는 동작하지 않을 수 있다.
- `browser_dom` 모드는 자막 텍스트가 DOM 또는 `textTracks.activeCues`에 노출되는 사이트에서만 동작한다. 자막이 캔버스, DRM 경로, 폐쇄형 플레이어 내부에만 있으면 읽을 수 없다.
- Codex OAuth 경로는 공식 Platform API 키 경로가 아니므로 계정 권한, 모델 카탈로그, 백엔드 변경에 영향을 받을 수 있다.
- 실시간 번역 품질과 지연 시간은 OCR 속도, 네트워크, 모델 응답 시간에 좌우된다.

## 9. 향후 개선

- 자막 영역 자동 감지
- 언어 자동 감지
- 번역 이력 패널
- Anki 카드 생성
- SRT/CSV 저장
- 로컬 번역 모델 지원
