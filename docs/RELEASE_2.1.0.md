# BeyondPack 2.1.0 (사전 릴리스)

국가를 먼저 선택한 뒤 `FNSKU|CountryCode` 복합키로 상품을 찾는 Windows 포장 프로그램입니다. 동일한 FNSKU가 여러 국가에서 사용되어도 품목코드와 SKU를 정확히 구분합니다.

## 주요 기능

- 실행 시 SharePoint `Product_Published` 상품 데이터 다운로드
- 네트워크 장애 시 검증된 로컬 SQLite 캐시 사용
- 품목명, FNSKU, 품목코드, SKU, 국가명 표시
- 박스수량, 무게, 가로·세로·높이, 박스당 상품수량 작업자 입력
- 여러 FNSKU 합포, 임시저장 복구, Excel 실적 및 박스 라벨 출력
- 기존 SchemaVersion 1 캐시 읽기 호환

## 다운로드 선택

- `BeyondPack-2.1.0-Windows-x64-portable.zip`: 현장 배포 권장. 압축을 푼 뒤 `BeyondPack/BeyondPack.exe`를 실행합니다.
- `BeyondPack-2.1.0-Windows-x64.exe`: 단일 파일. 첫 실행 시 내부 파일 추출 때문에 시작이 더 느릴 수 있습니다.
- `SHA256SUMS.txt`: 다운로드 파일 무결성 확인용입니다.

## 운영 전 필수 설정

`config.example.json`의 SharePoint 값을 실제 값으로 바꿔 `%LOCALAPPDATA%\BeyondPack\config.json`에 저장해야 합니다. 필요한 값은 Entra `tenant_id`, Public client `client_id`, SharePoint `site_id`, `list_id`입니다.

이 빌드는 코드서명되지 않았으며 실제 스캐너·프린터·라벨·대량 데이터 UAT 전 단계입니다. Windows SmartScreen 경고를 조직 정책으로 무시하거나 백신 예외를 무분별하게 배포하지 말고, 사내 코드서명 및 승인 절차를 거친 뒤 운영 전환하십시오.
