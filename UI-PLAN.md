# UI 개선 계획: tkinter → NiceGUI

현재 창(`mail_assistant/app.py`, 약 930줄)을 NiceGUI 기반 로컬 웹 UI로 옮기고,
그 과정에서 지금 화면이 놓치고 있는 기능을 채운다. 이 문서는 결정 사항과 작업
목록이며, 완료된 항목은 체크박스를 채우고 문서는 유지한다.

전환 근거, 대안 검토(CustomTkinter / PySide6 / Flet), 비용은 6절과 7절에 있다.
사용자에게 보이는 동작·설치·문제 해결은 `README.md`, 조용히 깨지는 제약은
`CLAUDE.md`가 계속 정본이다.

---

## 1. 요약

- **바꾸는 것**: `app.py`(창)와 `style.py`의 tkinter 색 변환부. 화면 전체.
- **바꾸지 않는 것**: `core.py`(DB), `worker.py`(수집·분석·반영 루프), `services.py`
  (POP3·Codex·자격 증명), `excel.py`·`dashboard.py`·`calendar_sheet.py`(COM),
  `update.py`(업데이트), `report.py`(오류 보고), 설치·릴리스 구조.
- **1단계 산출물**: `mail_assistant/webui.py`(현황 페이지), `tests/test_webui.py`,
  `packaging/entry_web.py`·`spike_web.spec`·`spike.ps1`·`shoot.ps1`·`requirements-spike.txt`.
  스파이크 파일은 전환을 접으면 지운다. `requirements.txt`는 아직 건드리지 않았다
- **재사용하는 것**: `overview.py`, `calendar_sheet.collect()`, `dashboard.CATEGORIES`
  /`PRIORITIES`, `settings.normalize()`, `core.row_view()`/`filter_rows()`.
  화면 산술을 순수 모듈로 분리해 둔 덕분에 데이터 계층은 그대로 쓴다.
- **부수 효과(큰 이득)**: NiceGUI 페이지 함수는 win32 없이 임포트되므로, 지금까지
  Windows에서만 검증되던 화면 로직을 Linux CI의 단위 테스트로 덮을 수 있다.

---

## 2. 아키텍처: 워커는 프로세스 소유의 전역 싱글턴

브라우저 탭은 닫히고 새로 열리고 새로고침된다. 그때마다 워커가 죽거나 두 개가
뜨면 안 된다. 지금은 창이 워커를 소유한다 — `App.start()`가 스레드를 만들고
`App.close()`가 멈춘다. 이 소유권을 프로세스로 올린다.

| 지금 (tkinter) | 전환 후 (NiceGUI) |
|---|---|
| `App.__init__`이 스레드 기동 | `app.on_startup`에서 모듈 레벨 싱글턴 기동 |
| `self.events` 큐 → `App.poll()` (`app.py:884`) | 전역 Hub(공유 상태 dict + 로그 링버퍼), 클라이언트별 `ui.timer`가 읽음 |
| `notify=self.events.put` (`worker.py:18`의 `notify` 인자) | `notify=hub.publish` |
| 창 닫기 = 워커 정지 (`app.py:914`) | 탭 닫아도 계속 폴링. 종료는 트레이 아이콘 또는 명시적 종료 |
| 로그가 Text 위젯 안에만 존재 (`app.py:555`) | DB 또는 전역 버퍼. 재연결 시 다시 그린다 |

tkinter 창은 **의도적으로 지금 동작을 유지한다** — 그 창이 곧 앱이기 때문이다. "탭을 닫아도
계속"은 웹이 화면을 가져가는 5단계에서 트레이 종료와 함께 바꾼다.

**DB가 정본이라는 기존 원칙이 그대로 재연결 모델이 된다.** `refresh()`가 이미
DB에서 전부 다시 계산하므로 재연결 복구 로직을 새로 만들 필요가 없다.

### 2.1 화면에만 사는 상태 (이관 대상)

- [x] 실행 로그 → `log` 테이블 + `core.LogStore`. `Hub.recent()`가 꺼내고
      `App.replay()`가 창을 열 때 다시 그린다. `status.json`은 `MailAssistantTools paths`용
      디버그 산출물로 남기고, 화면은 `last_fetch`/`last_export` meta를 읽는다
- [ ] 선택된 메일 / 검색어 / 정렬 / 표시 중인 달 → URL 쿼리 또는 `app.storage.user`.
      해당 화면을 웹이 가져가는 3·4단계에서 함께 한다
- [ ] 업데이트 다운로드 진행률 (`progress` dict) — 아직 `App` 안에 있다. 업데이트 화면을
      옮기는 5단계에서 함께 한다

### 2.2 Codex 동시 호출 잠금

- [x] **Codex 호출 전역 세마포어(동시 1개)** — `services.codex_slot()`.
      `analyze()`는 기다리고, 표시등은 `check_login(timeout=2)`으로 물어보기만 하고
      점유 중이면 `확인 중`으로 답한다. `CodexBusy`는 `RuntimeError`의 하위라서
      `login_state()`에서 반드시 먼저 잡아야 한다

---

## 3. 현재 탭의 결함

코드에서 확인한 것만 적는다. 미관이 아니라 기능적 손해가 있는 항목이다.

### 현황 탭

- [x] **D1** 막대 그래프 픽셀 하드코딩 — `112 + int(160 * value / top)` (`app.py:177`).
      창을 넓혀도 그래프는 고정, 한글 라벨이 길면 겹친다. → 반응형 차트.
- [x] **D2** 카드를 클릭할 수 없다 (`app.py:131`). "긴급·높음 12"를 보고 그 12건을 보려면
      메일 탭에서 직접 필터를 걸어야 한다. → 카드 클릭 = 필터된 목록으로 이동.
- [x] **D3** **지난 마감이 앱에 보이지 않는다.** `calendar_sheet.overdue()`
      (`calendar_sheet.py:97`)는 있고 테스트도 있는데 엑셀에서만 쓴다. 앱은
      `deadlines()`(`overview.py:42`)로 오늘~+7일만 본다. 기한을 넘긴 일이 화면에서
      사라지는 것은 업무 도구로서 가장 나쁜 누락이다.
- [x] **D4** `Store.day_counts()`(`core.py:203`)가 **사용되지 않는다**(테스트만 존재).
      수집·분석·반영 일별 추이를 낼 재료가 있는데 화면이 쓰지 않는다.
- [x] **D5** 마감 창이 `DUE_DAYS = 7` 상수 고정(`overview.py:14`). 화면에서 14일/30일
      토글이 불가능.

### 메일 탭

- [x] **D6** **실패한 메일만 골라볼 수 없다.** `STATES`는 `('', '분석 대기', '미처리', '처리')`
      (`app.py:31`)인데 `state_of()`는 `f'{n}회 실패'`를 반환한다(`core.py:231`).
      재분석이 가장 필요한 대상이 필터에서 빠진다. '분석 대기'를 골라도 실패 건은 안 나온다.
- [x] **D7** 검색이 제목·발신자만 본다(`core.py:250`). 본문·요약·요청사항은 검색되지 않는다.
      `parsed`/`result`가 DB에 있으므로 SQL LIKE 또는 FTS5로 확장.
- [x] **D8** 키 입력마다 2000행을 파이썬에서 필터·정렬·전체 재삽입(`app.py:212` →
      `paint_list`, `core.py:176`의 `limit=2000`). 수천 건에서 타이핑이 밀린다.
      → 검색·정렬·페이징을 SQL로 내리고 Quasar 테이블의 서버 사이드 모드 사용.
- [x] **D9** **초안 편집이 조용히 사라진다 (데이터 손실).** `show_mail()`이 초안 위젯을
      무조건 지우고 다시 채운다(`app.py:321`). 그리고 `poll()`은 워커가 보고할 때마다
      `show_mail(self.selected, focus=False)`를 다시 호출한다(`app.py:906`). 즉
      **초안을 고치는 중에 워커 주기가 돌면 입력이 덮어씌워진다.** 경고도 없다.
      → 더티 상태 추적 + 자동 저장, 편집 중 갱신 보류.
- [x] **D10** 일괄 작업이 없다. `Store.reset(ids)`(`core.py:192`)는 리스트를 받도록
      이미 만들어져 있는데 UI는 한 건씩만 보낸다. "실패 20건 전부 재분석" 불가.
- [x] **D11** 첨부 파일이 상세 영역에 없다. '원문 보기' 별도 창(`app.py:358`)에만 있다.
- [x] **D12** 본문·초안이 `height=6`/`height=5` 위젯에 눌려 있다. 원문은 별도 창으로만
      볼 수 있다. → 분석 결과와 원문을 나란히, 각자 스크롤.
- [x] **D13** 답장이 `mailto:`뿐(`app.py:346`, `excel.py:122`). 긴 초안은 URL 길이 제한에
      걸린다. → 클립보드 복사 버튼 추가.

### 일정 탭

- [x] **D14** 캔버스 수작업의 한계: 셀 118×84 고정(`app.py:34`), 항상 6주, 하루 3건까지,
      제목은 `entry.label[:16]`로 절단(`app.py:434`).
- [x] **D15** 클릭이 텍스트를 정확히 맞춰야 동작한다 — `click_day`가
      `find_withtag('current')`의 태그를 본다(`app.py:442`). 셀 빈 곳을 더블클릭하면
      아무 일도 없다. 발견하기 어려운 조작.
- [x] **D16** 월간 뷰만 있고 주간·일별·아젠다가 없다. 원문 근거(`evidence`)가 달력에
      전혀 노출되지 않는다(툴팁 자리).

### 실행 탭

- [x] **D17** 로그가 재시작하면 전부 사라진다(메모리 Text, 500줄 초과 시 앞부분 삭제,
      `app.py:555`). 어제 무슨 오류가 났는지 확인할 방법이 없다.
- [x] **D18** 다음 확인까지 남은 시간이 보이지 않는다(`interval` 기본 180초). "멈춘 건가?"
      상태가 생긴다. → 카운트다운.
- [x] **D19** 분석 진행률이 없다. 워커가 `'메일 분석 중…'` 문자열만 보낸다(`worker.py:44`).
      `pending()`은 LIMIT 5이므로 n/m과 대상 제목을 보낼 수 있다.
- [x] **D20** '마지막 확인' 램프가 DB의 `last_fetch` meta가 아니라 **로컬 시계 문자열**이다
      (`app.py:901`). 재연결하면 의미가 없어진다. `last_export`는 아예 표시되지 않는다.
- [x] **D21** 버튼 6개가 한 줄에 평면 배치, `update_buttons()`는 4개만 상태 관리
      (`app.py:648`).

### 설정 탭

- [x] **D22** `찾기` 버튼 위치가 `row=5` 하드코딩(`app.py:502`). `FIELDS` 순서를 바꾸면
      엉뚱한 줄에 붙는다. 조용히 깨지는 종류.
- [x] **D23** 검증이 저장 시점 대화상자 한 번뿐(`settings.normalize`). 어느 필드가 틀렸는지
      필드 옆에 표시되지 않는다. → 인라인 검증.
- [x] **D24** **건너뛴 버전을 UI로 되돌릴 수 없다.** `update_skip`은 쓰기만 하고
      (`app.py:778`) 지우는 화면이 없어 `config.json`을 직접 편집해야 한다.
- [x] **D25** 저장된 비밀번호를 **삭제하는 방법이 없다**(`services.save_password`만 존재).
      계정을 바꾸면 이전 자격 증명이 남는다. → `delete_password()` 추가 + 설정에 버튼.
- [ ] **D26** 엑셀 파일 선택이 `asksaveasfilename`(`app.py:513`). 기존 파일을 고르는
      동작인데 "저장" 대화상자가 뜬다. **웹 설정에는 파일 선택 대화상자가 없다** —
      브라우저는 서버의 경로를 고를 수 없으므로 경로를 직접 입력받는다. tkinter 쪽
      대화상자 종류는 그대로 남아 있다.
- [x] **D27** 데이터 폴더 경로·버전·`config.json` 위치가 화면에 없다(README에만 있다).

---

## 4. 신규 페이지

### P1. 할 일 (칸반) — 우선순위 1

빈 투두가 아니라 **메일에서 나온 할 일**이어야 한다. 재료가 이미 DB에 있다:
`result['next_action']`, `result['requests']`, `events[].deadline`, `handled`.

- [x] 소스: 분석된 메일의 `next_action` + 수동 추가 항목
- [x] 칸반 3열: 지금 `handled`는 `''`/`'처리'` 둘뿐이다. **중간 상태 `'진행'`을 추가**하면
      3열이 된다. `HANDLED = '처리'`를 그대로 두면 `state_of()`, `Store.open_count()`,
      엑셀 '처리 상태' 열이 전부 호환된다.
- [x] 수동 항목용 새 테이블. `Store.__init__`의 `executescript`(`core.py:77`)에
      `CREATE TABLE IF NOT EXISTS todo (...)`를 추가한다. `CLAUDE.md`의
      "Schema changes are ALTER TABLE ADD COLUMN only"는 **`mail` 테이블을 재구축하지
      말라**는 뜻이며, 새 테이블 추가는 이를 위반하지 않는다. `migrate()`는 컬럼 추가
      전용으로 유지한다.
- [ ] 드래그는 **놓는 즉시 DB에 커밋**한다. — 미구현. nicegui 3.16에 sortable 엘리먼트가
      없어 ◀ ▶ 버튼으로 옮긴다. 누르는 즉시 커밋되므로 새로고침에 되돌아가지 않는다. 그러지 않으면 새로고침에 되돌아가는 장난감이 된다.

### P2. 답변 초안 검토 큐 — 우선순위 2

`review_pending()`(`overview.py:67`)이 "검토 전 초안" 개수를 세지만 **그 목록을 보는
화면이 없다.** 엑셀에는 `답변 초안` 시트가 있는데 앱에는 없다.

- [x] 초안을 한 건씩 넘기며 수정 → 저장 / 복사 / 처리 완료
- [x] 입력을 멈추면 자동 저장되고, 저장되면 큐에서 빠진다

### P3. 진단 — 우선순위 3

`diagnose.py`가 이미 POP3 단계별 점검, 엑셀 헤더 점검, 저장된 분석 결과 확인을
수행하는데 **CLI로만 접근 가능**하다(README "오류 해결" 표가 계속
`MailAssistantTools.exe`를 안내하는 이유).

- [x] 점검 실행 버튼 + 결과 출력을 탭으로 올린다. 출력이 이미 텍스트라 이식이 쉽다
- [x] POP3 점검 단계를 `services.connection_steps()`로 빼서 화면이 쓴다

### P4. 채팅 (Codex) — 우선순위 4, 제약 있음

가능하다. 단 아래 세 가지를 전제로 설계한다.

- [x] **스트리밍 없음.** `codex exec`는 원샷이고 결과를 파일로 받는다(`services.py:164`).
      채팅은 `--output-schema`를 떼고 자유 텍스트를 받되, 프로세스가 끝나야 응답이 나온다.
      턴마다 스피너 + 수 초~수십 초. 토큰 단위로 흐르는 경험은 기대하지 않는다.
- [x] **멀티턴은 직접 관리.** 대화 기록을 sqlite에 쌓고 매 턴 전체 기록 + 선택된 메일
      내용을 다시 넣는다. 세션 재개 플래그는 CLI 버전에 의존하므로 쓰지 않는다.
- [x] **전역 세마포어 필수** (2.2절). 워커 분석과 채팅이 같은 한도를 놓고 싸운다.
- [x] **보안 설정을 절대 완화하지 않는다.** 채팅 프롬프트에도 `analyze()`의 프롬프트
      인젝션 방어문(`services.py:146` 이하)을 그대로 넣고, `--sandbox read-only`,
      `features.shell_tool=false`, `approval_policy="never"`를 유지한다. 채팅 맥락에는
      신뢰할 수 없는 메일 본문이 들어가고, 도구를 켜주면 그것이 곧 실행 경로가 된다.

### P5. 통계 — 마지막

- [x] `Store.day_counts()`(D4) 기반 수집·분석·반영 추이 + 카테고리·우선순위 변화

### 만들지 않는 것

- 별도 검색 탭 → 메일 탭에 흡수
- 별도 대시보드 탭 → 현황 탭이 그 역할

---

## 5. 개별 기능 결정

### 5.1 연결 테스트와 웹 UI 인증은 다른 문제다

섞기 쉬우므로 분리해 적는다.

- **POP3 연결 테스트** (`services.check_connection`): 지금도 동작하며 설정 탭의
  입력값으로도 테스트된다(`test_typed`). 개선점은 대화상자 대신 화면 안에 단계별
  결과를 보여주는 것 — P3와 로직을 공유한다.
- **웹 UI 게이트** (전환으로 *새로 생기는* 요구): 메일 본문이 로컬 HTTP로 서빙되므로
  같은 PC의 다른 프로세스가 접근할 수 있다.
  - [ ] **`127.0.0.1` 바인딩 + 랜덤 포트 + 실행 시 생성한 1회용 토큰**
  - [ ] `0.0.0.0` 바인딩 금지 — 방화벽 프롬프트, 사내망 노출, "메일은 Codex 외부로
        나가지 않는다" 불변조건 위반
  - [ ] `app.storage` 사용 시 `storage_secret` 설정. 1인용 데스크톱 앱에 매번 비밀번호를
        묻는 로그인 화면은 마찰만 되므로 쓰지 않는다

### 5.2 FullCalendar 도입

D14~D16이 한 번에 해소된다. 매핑이 깔끔하다.

- [ ] 이벤트 소스는 `calendar_sheet.collect()` → `{date: [Entry]}`를 **그대로 재사용**.
      `Entry.kind`(마감/시작/확인 필요)를 색으로 쓰고 `COLORS`를 유지한다
- [ ] 클릭 → 해당 메일로 이동, 툴팁 → `evidence`, 월/주/아젠다 뷰 확보, 지난 마감도
      자연히 보인다(D3)
- [ ] **JS/CSS를 저장소에 포함해 로컬 서빙**(`app.add_static_files`)하고 spec의 `datas`에
      추가한다. 예제는 CDN을 쓰지만 이 앱은 오프라인·프록시 뒤에서도 떠야 한다.
      **모든 CDN 기반 엘리먼트에 공통으로 적용하는 규칙으로 삼는다**
- [ ] 표준 뷰만 사용(MIT). 프리미엄 타임라인·리소스 뷰는 유료이므로 쓰지 않는다
- [ ] 설치한 NiceGUI 버전이 `ui.fullcalendar`를 기본 제공하는지 실측 확인. 없으면 예제처럼
      JS를 직접 얹는다

---

## 6. 전환 비용 (감수하기로 한 것)

| 항목 | 내용 |
|---|---|
| **의존성** | nicegui → fastapi, uvicorn, starlette, pydantic, python-multipart, (native 모드) pywebview. `CLAUDE.md`의 "Stdlib only unless there is no alternative"에 대한 **의식적 예외**로 선언한다 |
| **번들 크기** | 현재 onedir 약 30MB → Vue/Quasar/Tailwind 정적 자산 + uvicorn 포함으로 수십 MB 증가. 무서명 설치본이라 다운로드 크기가 그대로 사용자 마찰이다 |
| **freeze** | `ui.run(reload=False)` 필수(리로더는 프로즌에서 재실행 루프). native 모드는 pywebview를 별도 프로세스로 띄우므로 엔트리 첫 줄에 `multiprocessing.freeze_support()` 필요. spec에 nicegui 정적 자산 수집 추가 |
| **selftest** | `CLAUDE.md` 규칙대로 **새 지연 임포트를 `selftest`에 등록**한다. nicegui·uvicorn·(pywebview)가 프리즈 후 임포트되는지는 CI Windows 레그만이 증명한다 |
| **로컬 HTTP 노출** | 5.1절의 loopback + 랜덤 포트 + 토큰 |
| **종료 의미 변경** | 지금은 창 닫기 = 전부 정지(`app.py:914`). 전환 후에는 "창은 뷰, 프로세스가 앱"이 되어 트레이 아이콘 또는 명시적 종료가 필요하다. 뮤텍스(`__main__.py`)와 업데이트 설치 순서(`finally`에서 `CloseHandle` → `update.launch`)는 **그대로 유지**하되, "워커 종료를 기다려 root.destroy" 부분은 서버 종료로 다시 쓴다 |
| **한글 폰트** | `('맑은 고딕', 9)` 하드코딩이 CSS 폰트 스택으로 넘어간다. 맑은 고딕/Pretendard 계열을 명시해야 한다 |

### 6.1 `CLAUDE.md` 갱신 필요 항목

- [ ] **`<<TreeviewSelect>>` 불변조건은 Treeview가 사라지면 무효**가 된다. 삭제하지 말고
      "왜 이런 종류의 되먹임을 경계해야 하는가"로 옮기거나, 위젯 제거 시점에 함께 제거한다
- [ ] "The window lives in `app.py`" 항목을 서버·페이지·Hub 구조로 갱신
- [ ] "Stdlib only" 항목에 nicegui 예외를 명시
- [ ] 자격 증명 불변조건(`read_password`의 `LookupError`/winerror 1168)은 services 계층이므로
      **그대로 유지**된다

### 6.2 검토했으나 채택하지 않은 대안

- **CustomTkinter**: 코드 변경은 가장 적지만 쓸 만한 표 위젯이 없어 `ttk.Treeview`를
  끼우게 되고, 메일 목록이 주인공인 이 앱에서 그 하나가 전체를 망친다.
- **PySide6**: 가장 튼튼하고 대량 표에 유리하지만 전면 재작성 + 100MB대 번들이며,
  기본 상태는 평범해서 결국 꾸며야 한다.
- **Flet**: 손대지 않아도 가장 예쁘지만 Flutter 엔진 번들로 설치본이 크고 API 변동이 잦다.

---

## 7. 작업 순서

각 단계는 앞 단계가 끝나야 시작한다. 1단계가 통과하지 못하면 전환을 접는다.

### 0단계 — 툴킷과 무관한 즉시 수정 (완료)

NiceGUI 결정을 기다릴 이유가 없는, 지금 손해를 보고 있는 항목.
D3·D6은 순수 모듈이라 테스트로 덮었고(123개), D9·D22는 `app.py`라 Windows에서
수동 확인이 필요하다.

- [x] **D9** 초안 편집 덮어쓰기 (데이터 손실)
- [x] **D6** 실패 건 필터 불가
- [x] **D3** 지난 마감 미표시
- [x] **D22** `찾기` 버튼 `row=5` 하드코딩

### 1단계 — 스파이크 (여기서 중단 판단)

- [x] nicegui 설치, `현황` 페이지만 이식 — `mail_assistant/webui.py`.
      `overview()` 결과를 그대로 연결하고 `calendar_sheet`·`dashboard.describe`를 재사용
- [x] Linux 프리즈로 플랫폼 무관 자산 실측 (아래 표)
- [x] **Windows 프리즈 + native 모드 실측** — 아래 표. Windows 11 26200 /
      Python 3.13.15 / PowerShell 5.1 / nicegui 3.16.0 / pyinstaller 6.22.2 / pywebview 5.4
- [x] 실측치는 감수 가능하다 — **+10.8 MB**. 전환을 계속한다

#### 실측 결과 (2026-09-11, Windows 11 26200, Python 3.13.15, nicegui 3.16.0)

| 항목 | Windows 실측 | Linux 참고 |
|---|---|---|
| 현재 출하본 onedir | **37.9 MB** | — |
| 스파이크 onedir (native 포함) | **48.7 MB** | 95 MB |
| 스파이크, 벤더 JS 미제외 | — | 115 MB |
| 그중 nicegui | 5.8 MB (static 5.6 / elements 0.2) | 6.2 MB |
| pywebview 전용 (pythonnet+webview) | 4.3 MB | 해당 없음 |
| 출하본의 tcl/tk (tkinter) | 3.7 MB | 해당 없음 |
| 프리즈 시간 (스파이크 / 출하본) | 49.8초 / 31.0초 | — |
| 콜드 스타트 (URL 출력까지) | **2.5초**, 첫 200 응답 +0.1초 | — |

**증가분은 +10.8 MB (+28%).** 전환 후 실제 번들은 출하본에 nicegui 스택을 더하고
tkinter(3.7 MB)를 빼는 형태이므로 **대략 45~50 MB**가 된다. 브라우저 모드로만 가면
pywebview 4.3 MB가 더 빠진다. 처음 우려했던 2~3배가 아니다.

Linux 실측이 크게 나온 이유는 그쪽에만 있는 것들(uvloop 13 MB, cryptography 14 MB,
libpython·libcrypto 등) 때문이고, Windows 빌드는 이것들을 끌어오지 않는다. **Linux 숫자로
판단하면 안 된다.**

가장 큰 절감은 **벤더 JS 제외(-21 MB)** 였다. nicegui는 제공하는 모든 엘리먼트의 JS를
동봉하는데 이 앱은 큰 것들을 하나도 쓰지 않는다. 단 `pyinstaller-hooks-contrib`의
`hook-nicegui.py`가 `collect_data_files('nicegui')`를 그대로 호출하므로 **spec 단계에서
거른 것은 훅이 다시 넣는다.** `Analysis.datas`를 만든 뒤 걸러야 한다 — `spike_web.spec` 참조.

남은 큰 항목과 추가 절감 여지:

| 항목 | 크기 | 비고 |
|---|---|---|
| cryptography | 14 MB | httpx·aiohttp의 urllib3/pyopenssl 선택 경로. 제외 가능한지 확인 필요 |
| pygments | 8.7 MB | `ui.code` 하이라이팅용. 쓰지 않으면 제외 후보 |
| _brotli | 5 MB | aiohttp 경유 |
| docutils | 4.2 MB | `ui.restructured_text`용. 쓰지 않으면 제외 후보 |

#### 확인한 것

- **테스트 136개가 Windows에서도 통과한다.** `app.py`도 임포트되고 `STATES`에 `'실패'`가
  들어간 것을 실기에서 확인했다 (0단계 D6)
- **페이지가 실제로 렌더된다.** Edge 헤드리스로 스크린샷을 찍어 확인했다 —
  `packaging/shoot.ps1`. 데스크톱은 찍지 않고 페이지만 렌더한다
- **토큰 게이트가 동작한다.** `?t=` 없이 열면 데이터가 렌더되지 않는다 (nicegui `User`
  시뮬레이터로 검증)
- **프리즈된 exe가 뜨고 서빙한다.** 셸 HTML이 참조하는 자산 8개 전부 200, 404 0개 —
  DROP 목록이 필요한 자산을 지우지 않았다
- **native 모드가 뜬다.** 부모 프로세스 + pywebview 자식 프로세스가 생기고 자식의
  창 제목이 `메일 도우미 0.2.1`이다. `multiprocessing.freeze_support()`가 동작한 증거다
- **loopback만 바인딩하고 방화벽 규칙이 생기지 않는다** (`127.0.0.1`, 매 실행 랜덤 포트,
  `Get-NetFirewallApplicationFilter`에 항목 없음)

#### 스크린샷에서 잡은 결함 (수정 완료)

시각 확인이 실제로 값을 했다. 셋 다 코드로는 드러나지 않았다.

- 화면 전체가 중앙 430px 리본에 눌려 있었다. NiceGUI 기본 컨텐츠 컨테이너가 flex column
  이라 `max-width`만으로는 늘어나지 않는다 → `width:100%` 필요
- 최근 7일 수신의 숫자가 고정 높이 플롯 박스 위에 떠서 막대와 떨어져 보였다 →
  `justify-content:flex-end`로 숫자와 막대를 같이 바닥에 붙였다
- 바 레일이 넓은 패널 폭을 전부 차지했다 → `max-width:320px`

#### 확인하지 못한 것

- 실기 눈 검사. 스크린샷은 1280px 한 폭만 찍었다. 좁은 창(800px 이하)에서의 줄바꿈과
  실제 맑은 고딕 렌더는 사람이 봐야 한다
- Windows Defender가 처음 exe를 검사할 때의 체감 지연 (측정값 2.5초는 검사 후 재실행 기준)

### 2단계 — 아키텍처 이관 (완료)

- [x] Hub + 전역 워커 싱글턴 (2절) — `mail_assistant/hub.py`
- [x] 로그를 DB로 (D17), 시각은 meta에서 (D20)
- [x] Codex 세마포어 (2.2)
- [x] tkinter 창과 웹 페이지가 **같은 Hub**를 본다. 웹의 실행 상태 strip은 읽기 전용이다
- [x] 테스트 161개 (Linux·Windows 모두). `hub.py`는 `run`을 주입받아 win32 없이 테스트된다

Windows에서만 드러난 결함 하나를 잡았다: 워커 스레드가 자기 `LogStore` 연결을 닫지 않아
`mail.db`가 프로세스 수명 내내 잠겼다. Linux는 그대로 통과하고 Windows는
`TemporaryDirectory` 정리에서 WinError 32로 실패한다 — **테스트를 Windows에서도 돌려야
하는 이유**다.

### 3단계 — 메일 페이지 (완료)

- [x] 테이블 + SQL 검색·필터·정렬·페이징 (D6, D7, D8) — `Store.search()`
- [x] 상세 레이아웃, 첨부, 원문 병렬 표시 (D11, D12)
- [x] 자동 저장 (D9), 복사 버튼 (D13), 일괄 작업 (D10)
- [x] 선택한 메일·검색어·정렬·페이지를 `app.storage.user`에 유지 (2.1절의 이관 항목)
- [x] 현황의 마감 목록을 클릭하면 그 메일이 열린 메일 페이지로 이동한다 (D2의 절반)

`category`·`priority`를 컬럼으로 비정규화했다. `subject`·`sender`가 이미 같은 이유로
컬럼인 것과 같은 패턴이고, 이게 있어야 필터·정렬이 모든 행의 JSON을 파싱하지 않는다.
`migrate()`가 기존 메일을 backfill한다.

**검색의 알려진 한계**: `parsed`·`result` JSON 전체를 LIKE로 훑기 때문에 본문·요약·
요청사항이 모두 걸리는 대신, `subject` 같은 영문 JSON 키 이름을 검색하면 전부 걸린다.
한글 질의로는 문제되지 않는다. 제대로 고치려면 FTS5가 필요하고, 그건 별건이다.

스크린샷으로 잡은 결함 셋:
- 테이블이 옆으로 넘쳐 종류·우선순위·상태 컬럼이 잘렸다 → 컬럼 폭 고정 + 제목 말줄임
- 원문 박스가 텅 빈 채로 열렸다. CSS가 아니라 **본문 텍스트** 문제였다 — HTML 메일이
  `TextHTML`을 지나면 원본 들여쓰기와 빈 줄이 그대로 남는다 → `tidy_body()`
- 긴 URL·주소 줄이 박스를 넘쳤다 → `overflow-wrap:anywhere`

### 4단계 — 일정 페이지 (완료)

- [x] FullCalendar 6.1.19을 `mail_assistant/vendor/`에 넣고 `/vendor`로 서빙 (CDN 없음)
- [x] `calendar_sheet.collect()`를 그대로 재사용. `Entry`에 `evidence`를 기본값으로 추가해
      툴팁에 원문 근거를 띄운다 (D16)
- [x] 월·주·목록 뷰, 한글 요일·버튼, 월요일 시작, 지난 마감은 취소선 + 아래 별도 목록

**여기서 하루를 태울 수 있는 함정을 하나 밟았다.** 이벤트를 `ui.run_javascript`로
넘겼더니 아무것도 그려지지 않았다. 그 호출은 연결된 클라이언트를 필요로 하는데, 그 시점이
페이지가 다 만들어진 뒤라서 초기화 함수가 호출되지 않았다. 페이로드를 `add_body_html`에
같이 실어 스크립트가 스스로 초기화하게 바꾸고, `<script src>`를 head로 올려 라이브러리가
먼저 정의되도록 했다. 실패하면 `data-state`와 화면 문구로 드러난다.

### 5단계 — 현황 / 실행 / 설정 페이지 (완료)

- [x] 현황: 반응형 차트, 카드 클릭 이동, 마감 창 7/14/30일 토글, 일별 추이 (D1, D2, D4, D5)
- [x] 마감 목록을 체크리스트로 — 체크는 메일의 `handled`를 쓰고, 끝난 줄은 창 안에
      남겨 진행 막대가 가리킬 것을 남긴다 (`overview.due_window`)
- [x] 현황이 못 보던 것 세 가지 — 분석 실패(있을 때만 뜨는 다섯 번째 카드), 가장
      오래된 미처리의 경과일, 할 일 판 요약. `최근 7일 수신`은 `일별 처리량`의 수집
      선과 같은 창의 같은 측정값이라 뺐다 (`recent_option`도 함께 지웠다)
- [x] 실행: 다음 확인 카운트다운, 분석 진행률(`worker.py`가 n/m과 제목을 보낸다),
      램프 정리, 설정 미비 시 시작을 막고 이유를 적는다 (D18, D19, D21)
- [x] 설정: 항목별 즉시 검증(`settings.field_errors()`), 건너뛴 버전 해제,
      비밀번호 저장·삭제(`services.delete_password()`), 경로 표시 (D23~D25, D27)
- [ ] **업데이트 화면은 옮기지 않았다.** 설치는 창을 닫고 설치 프로그램을 띄우는
      과정이고, 그 인계는 `__main__.py`의 뮤텍스·`finally`가 쥐고 있다. 웹이 기본
      화면이 될 때 함께 옮기는 것이 맞다. 지금은 웹에서 "건너뛴 버전 초기화"만 된다.

### 6단계 — 신규 페이지 (완료)

- [x] P1 할 일(칸반) → P2 초안 검토 큐 → P3 진단

### 7단계 — 채팅과 통계 (완료)

- [x] P4 상담 — `services.chat_reply()`. `analyze()`와 같은 하드닝 플래그를 쓰고,
      출력 스키마를 `{reply: string}` 하나로 제한해 CLI 출력을 긁지 않는다
- [x] P5 통계 — 7·30·90일 추이 + 분포

### 8단계 — 마무리 (완료)

- [x] `CLAUDE.md` 갱신 — 새 불변조건 7개
- [x] `README.md` — 웹 화면 절, 문제 해결 표, 인수 테스트 11~16번
- [x] 화면 단위 테스트 247개 (Linux·Windows 양쪽)
- [x] `MailAssistantTools.exe demo` 회귀 확인 — **기존 버그를 하나 찾아 고쳤다**(아래)
- [x] 출하 빌드에 `MailAssistantTools.exe web` 추가, `requirements.txt`에 nicegui 선언,
      spec에 vendor·nicegui 처리, `selftest`에 항목 2개 추가

#### demo가 찾아낸 기존 버그

`excel.append_missing()`은 제가 건드리지 않은 코드였는데, 데이터 행이 **정확히 1개**인
시트에서 `'NoneType' object is not iterable`로 죽었다. COM은 한 셀 범위의 `.Value`를
스칼라로, 빈 한 셀은 `None`으로 돌려준다. 문자열이 들어 있었다면 더 나빴다 — 글자를
하나씩 돌면서 메일 ID를 글자와 비교했을 것이다. `excel.first_column()`으로 세 가지 모양을
정규화하고 `mail_rows()`도 같이 고쳤다. openpyxl 경로(`sample`)는 이 함수를 지나지 않아
아무 신호가 없었다.

#### 출하 빌드 실측 (정정)

| | |
|---|---|
| 이전 출하본 | 37.9 MB |
| nicegui 포함 | **59.6 MB** (+21.7 MB, +57%) |
| 프리즈 시간 | 46초 |
| `web` 콜드 스타트 | URL까지 1.1초, 열 페이지 전부 200 |

**1단계에서 낸 "+10.8 MB, 45~50 MB" 추정은 틀렸다.** 그 수치는 tkinter·pywin32·openpyxl을
제외한 스파이크 번들이었고, 실제 출하본은 두 UI를 모두 담는다. 벤더 JS 제외(-21 MB)와
pywebview 제외(-4 MB)를 적용한 뒤의 값이 위 표다.

### 9단계 — 화면 다시 그리기 (완료)

투박하다는 지적에서 출발했다. 원인은 nicegui의 한계가 아니라 `webui.py`가 컴포넌트를
거의 쓰지 않고 맨 `<div>` + 인라인 스타일 문자열로 그리고 있었다는 것이다.

- [x] **디자인 토큰 한 곳으로** — `webui.THEME` 한 장에 CSS 변수와 컴포넌트 클래스를
      모으고, 페이지는 `.classes()`를 먼저 쓴다. 색은 여전히 `style.py`에서 온다
- [x] **Quasar/Tailwind 사용** — 카드·태그·세그먼트 토글·아이콘 버튼·스티키 표 머리글.
      상단 바에 아이콘 네비게이션. `ui.colors()`로 Quasar 팔레트를 앱 색으로 맞췄다
- [x] **손으로 그리던 막대를 ECharts로** — `bar_list`·`column_list`·`trend_block`
      (div의 높이 %를 계산하던 코드)을 `bar_option`·`recent_option`·`trend_option`이
      돌려주는 옵션 딕셔너리로 바꿨다. 순수 함수라 Linux에서 단위 테스트가 된다
- [x] **번들에 echart 유지** — 두 spec의 `DROP_ASSETS`/`DROP`에서 `echart`를 뺐다.
      실측 1.8MB(dist 1.7MB). `selftest`에 `check_echart` 추가
- [x] **Pretendard 벤더링** — `mail_assistant/vendor/pretendard/`에 가변형 woff2 한 개
      (2.06MB, OFL-1.1). 한국어 서브셋 3종(787KB)은 KS X 1001 2,350자만 담아 메일 본문의
      희귀 음절이 폴백으로 튀어서 쓰지 않았다. 근거는 `vendor/README.md`에 표로 남겼다
- [x] 화면 단위 테스트 266개

#### 되짚어 둘 것 세 가지

**차트를 새로 만들지 않고 옵션만 갈아끼운다.** 현황은 5초마다 갱신되는데, echart를
`@ui.refreshable` 안에 두면 매 틱 캔버스가 사라졌다 다시 그려지며 등장 애니메이션까지
다시 돈다. `home()`은 차트 네 개를 한 번만 만들고 `paint()`가 옵션을 덮어쓴다.

**nicegui의 기본 레이아웃이 페이지와 싸운다.** `.nicegui-content`가
`align-items:start`인 flex column이라 상단 바가 가장 넓은 카드 너비로 줄어들고, 긴 로그
한 줄이 줄바꿈되지 않는다. 텍스트를 쌓는 표면은 `display:block`을 명시한다.

**웹폰트와 캔버스 차트는 서로를 기다리지 않는다.** `@font-face`만 선언하면 ECharts가
폴백 폰트로 라벨을 한 번 그리고 끝이라 페이지는 Pretendard, 차트는 맑은 고딕이 된다.
`<link rel="preload">`로 먼저 받게 했다. 그리고 `containLabel`이 잡는 축 여백은
ECharts가 추정한 라벨 높이 기준이라, Pretendard의 큰 라인 박스에서는 날짜가 캔버스
밖으로 잘린다 — `AXIS_GUTTER`와 추이 차트의 `left`가 그 몫이다.

#### 남은 것

- 칸반 드래그는 뒤에 붙였다(아래 미해결 질문의 마지막 항목)

---

---

## 8. 남은 결정과 미해결 질문

### 넘겨야 할 결정 하나

**웹 화면을 기본 창으로 삼을지는 결정하지 않았다.** 지금은 설치 마법사의 바로가기가
`MailAssistant.exe`(tkinter 창)를 열고, 웹은 `MailAssistantTools.exe web`으로 들어가는
두 번째 입구다. 기본을 바꾸는 일은 설치 바로가기, 자동 시작 인자, 업데이트 설치 인계,
트레이 종료가 한꺼번에 걸리는 되돌리기 어려운 변경이라 사람이 결정할 일로 남겼다.

바꾸기로 하면 필요한 작업: `__main__.py`가 창 대신 서버를 띄우고, 종료를 트레이나 명시적
종료로 옮기고, 업데이트 화면을 웹으로 옮기고(5단계 미완 항목), `installer.iss`의 바로가기와
`--autostart` 경로를 손보고, `app.py`와 `<<TreeviewSelect>>` 불변조건을 정리한다.

### 미해결 질문

- [ ] native 모드(pywebview 창) vs 기본 브라우저 — 설치 크기와 "앱처럼 보이는가"의 교환.
      1단계 실측 후 결정
- [ ] 트레이 아이콘을 넣을지(pystray 추가 의존성), 아니면 창 닫기 = 종료를 유지할지
- [x] 로그 보관 기간과 상한 — 최신 2000줄만 남기고 50줄마다 정리한다(`Hub.keep`)
- [x] 채팅 대화 기록의 보관 정책 — `mail.db`의 `chat` 테이블에만 남고, 화면의
      "대화 지우기"로 스레드 단위로 지운다. 자동 만료는 없다
- [x] 상담 말풍선을 `ui.chat_message`로, 답변은 `rich_text()`로 렌더 — `ui.markdown`은
      pygments 8.7MB를 설치본에 고정시키므로 쓰지 않는다(1단계 실측표의 제외 후보)
- [ ] 검색을 FTS5로 올릴지 (지금은 JSON 전체 LIKE, 3단계의 알려진 한계)
- [ ] `webui.py`가 1540줄이다. 페이지별로 쪼갤지 — 지금은 한 파일에서 shell·팔레트·
      상태를 공유해 이득이 있지만, 여기서 더 커지면 분리가 맞다
- [x] 할 일 카드 드래그 — nicegui에 sortable 엘리먼트가 없어 HTML5 drag 이벤트를
      `js_handler`로 직접 붙였다. 강조는 브라우저 안에서 끝나고 서버로 가는 것은
      드롭 한 번뿐이다. 화살표 버튼은 유일한 조작 수단이 사라지지 않도록 남겼다
