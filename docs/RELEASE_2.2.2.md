# BeyondPack 2.2.2

## 수정 사항

- Google Sheet 상품정보를 내려받은 뒤 `products.db` 교체 시 발생하던 `WinError 32` 수정
- 비상 Excel 상품 마스터가 동일한 파일 잠금으로 적용되지 않던 문제 수정
- SQLite 읽기·쓰기 연결을 작업 직후 명시적으로 종료하도록 변경
- 상품 조회와 상품DB 교체를 프로세스 내부 잠금으로 직렬화해 동시 접근 충돌 방지
- 백신 등 외부 잠금에 대비한 Windows 파일 교체 재시도 횟수 확대
- 상품DB뿐 아니라 포장DB와 진단 기능의 SQLite 연결도 확실히 종료하도록 개선

## 설치 방법

1. 작업관리자에서 실행 중인 `BeyondPack.exe`를 모두 종료한다.
2. `BeyondPack-2.2.2-Windows-x64-Setup.exe`를 실행해 기존 버전 위에 설치한다.
3. 바탕화면의 BeyondPack을 한 번만 실행한다.
4. Google Sheet 자동 업데이트 성공과 상품 건수·국가 목록을 확인한다.
5. 필요하면 `비상 Excel 업데이트`로 동일 상품 마스터를 적용해 확인한다.

기존 `%LOCALAPPDATA%\BeyondPack\data`의 상품DB·포장기록·Google Sheet 설정은 유지된다. `products.db`와 `packaging.db`를 삭제할 필요가 없다.
