# vendor

브라우저가 불러가는 서드파티 자산. **CDN을 쓰지 않는다** — 이 앱은 오프라인이거나
사내 프록시 뒤에서도 떠야 하고, CDN을 참조하면 그 환경에서 화면이 조용히 비어 버린다.

| 폴더 | 버전 | 라이선스 | 출처 |
|---|---|---|---|
| `fullcalendar/` | 6.1.19 | MIT (`LICENSE.md`) | `https://cdn.jsdelivr.net/npm/fullcalendar@6.1.19/` |
| `pretendard/` | 1.3.9 | OFL-1.1 (`LICENSE.txt`) | npm `pretendard@1.3.9`, `dist/web/variable/woff2/` |

표준 뷰만 쓴다. FullCalendar의 프리미엄 뷰(timeline·resource)는 유료이므로 도입하지 않는다.

## 폰트를 가변형 한 파일로 받은 이유

`PretendardVariable.woff2` 하나가 2.06MB이고 45~920 굵기를 전부 담는다. 대안이 둘 있었다.

| | 크기 | 문제 |
|---|---|---|
| 가변형 전체 (채택) | 2.06MB, 1개 | — |
| 정적 3종(Regular·SemiBold·Bold) | 2.34MB, 3개 | 더 크고 요청도 셋 |
| 한국어 서브셋 3종 | 787KB, 3개 | KS X 1001 2,350자만 담아 메일 본문의 희귀 음절이 폴백 폰트로 튄다 |

번들을 800KB 아래로 줄여야 할 일이 생기면 서브셋(`dist/web/static/woff2-subset/`)으로
바꾸고 `webui.THEME`의 `@font-face`를 굵기별로 셋으로 늘리면 된다. 한자는 어느 쪽을
골라도 Pretendard에 없어 폴백으로 간다.

올릴 때는 `webui.vendor_path()`가 이 폴더를 가리키고, 프리즈 빌드에서는
spec의 `datas`가 같은 위치에 복사한다.
