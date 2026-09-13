# vendor

브라우저가 불러가는 서드파티 자산. **CDN을 쓰지 않는다** — 이 앱은 오프라인이거나
사내 프록시 뒤에서도 떠야 하고, CDN을 참조하면 그 환경에서 화면이 조용히 비어 버린다.

| 폴더 | 버전 | 라이선스 | 출처 |
|---|---|---|---|
| `fullcalendar/` | 6.1.19 | MIT (`LICENSE.md`) | `https://cdn.jsdelivr.net/npm/fullcalendar@6.1.19/` |

표준 뷰만 쓴다. FullCalendar의 프리미엄 뷰(timeline·resource)는 유료이므로 도입하지 않는다.

올릴 때는 `webui.vendor_path()`가 이 폴더를 가리키고, 프리즈 빌드에서는
spec의 `datas`가 같은 위치에 복사한다.
