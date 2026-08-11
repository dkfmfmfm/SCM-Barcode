# BeyondPack 2.2.3

## 수정 사항

- 특정 PC에서 백신·백업 도구·잔존 프로세스가 `products.db`를 잠가 Google Sheet와 비상 Excel 업데이트가 실패하던 문제 제거
- 기존 상품DB를 덮어쓰지 않고 새 불변 SQLite 스냅샷을 만든 뒤 `products.current.json` 포인터만 전환하는 구조로 변경
- 현재 스냅샷이 손상·유실되면 직전 스냅샷 또는 기존 `products.db`로 자동 복구
- 현재·직전 2개 스냅샷만 유지하고 참조되지 않는 임시 DB는 안전하게 자동 정리
- 박스당 상품수량, 박스수량, 무게, 가로, 세로, 높이에 큰 `▲/▼` 버튼 추가
- 증감 버튼 클릭과 길게 누르기 동작 지원
- Windows GUI 자체검사에 박스당 상품수량·박스수량 증감 버튼 실제 클릭 검증 추가

## 설치 방법

1. 작업관리자에서 실행 중인 `BeyondPack.exe`를 모두 종료한다.
2. `BeyondPack-2.2.3-Windows-x64-Setup.exe`를 실행해 기존 버전 위에 설치한다.
3. 바탕화면의 BeyondPack을 한 번만 실행한다.
4. 창 제목이 `BeyondPack 2.2.3 · BEYOND EARTH`인지 확인한다.
5. Google Sheet 자동 업데이트와 상품 건수·국가 목록을 확인한다.
6. 숫자 입력칸의 `▲/▼` 버튼으로 값이 변하는지 확인한다.

기존 상품DB, 포장기록, Google Sheet 설정은 유지된다. `products.db`와 `packaging.db`를 삭제할 필요가 없다.
