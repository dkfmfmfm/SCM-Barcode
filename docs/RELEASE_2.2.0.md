# BeyondPack 2.2.0

## 주요 변경

- Google Sheet CSV 시작 자동 다운로드와 F2 수동 갱신
- 검증 후 로컬 SQLite 원자 교체, 실패 시 기존 DB 유지
- 동일 FNSKU의 국가별 조회와 `FNSKU+CountryCode` 중복 차단
- Excel 상품 마스터 비상 업데이트
- 오래된 캐시 경고 중에도 검증된 로컬 DB로 작업 가능
- SharePoint/MSAL 실행 의존 제거와 기존 설정 자동 마이그레이션
- 정식 Windows 설치파일, 바탕화면·시작 메뉴 아이콘, 제거 프로그램

## 권장 다운로드

`BeyondPack-2.2.0-Windows-x64-Setup.exe`를 설치한다. 최초 실행 후 `Sheet 설정`에서 `BeyondPack_Master` 탭 주소를 한 번 붙여넣는다.

포터블 ZIP과 단일 EXE도 제공하지만 일반 현장 PC에는 설치파일을 권장한다. 기존 `%LOCALAPPDATA%\BeyondPack\data`의 상품DB와 포장기록은 유지된다.
