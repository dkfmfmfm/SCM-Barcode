# BeyondPack 2.2.1

## 수정 사항

- Windows에서 여러 실행이 고정 `products.new.db`를 함께 사용해 발생하던 `WinError 32` 수정
- 상품DB 갱신마다 고유한 임시 SQLite 파일 사용
- 백신·SQLite 읽기 작업으로 파일이 잠긴 경우 원자 교체 자동 재시도
- BeyondPack 중복 실행 차단과 기존 실행 창 안내
- 실패한 임시파일 정리 오류가 정상 캐시를 손상시키거나 실제 동기화 오류를 가리지 않도록 개선

## 설치 방법

1. 작업관리자에서 실행 중인 `BeyondPack.exe`를 모두 종료한다.
2. `BeyondPack-2.2.1-Windows-x64-Setup.exe`를 실행해 기존 버전 위에 설치한다.
3. 바탕화면의 BeyondPack을 한 번만 실행한다.
4. 상품DB 업데이트 성공, 국가 목록, 상품 건수를 확인한다.

기존 `%LOCALAPPDATA%\BeyondPack\data`의 상품DB·포장기록·Google Sheet 설정은 유지된다. Google Sheet 탭 이름은 `BeyondPack`이며 신규 시트에는 `CountryName` 열이 필요하지 않다.
