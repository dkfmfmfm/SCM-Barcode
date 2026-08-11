# BeyondPack 2.1

`PLAN.MD`와 `RESEARCH.MD`를 기준으로 구현한 Windows 포장 작업 프로그램이다. 작업자가 국가를 선택한 뒤 FNSKU를 스캔하면 `FNSKU+국가` 복합키로 품목명·품목코드·SKU·국가명을 로컬 SQLite에서 즉시 조회하고, 박스수량·무게·치수·상품수량을 합포 단위로 저장한다.

> 현재 상태: 소스 기반 MVP. 실제 운영 전 SharePoint 앱 등록, 실제 스캐너·프린터 UAT, 기존 v1.4 라벨 양식 대조가 필요하다.

## 구현 범위

- 실행 시 SharePoint `Product_Published` 전체 동기화
- Microsoft Graph 페이지네이션 및 429/503 제한 재시도
- Windows DPAPI로 MSAL 토큰 캐시 보호
- 작업 시작 시 국가 선택 및 박스 구성 중 국가 변경 방지
- FNSKU 대문자·공백 정규화와 `FNSKU|CountryCode` 정확 일치 조회
- 동일 FNSKU의 국가별 상품 허용, 동일 FNSKU+국가 중복 차단
- 상품 수 20% 이상 급감 시 새 데이터 적용 차단
- `products.new.db` 검증 후 `products.db` 원자 교체
- 기존 DB를 `products.previous.db`로 한 세대 보관
- 네트워크 장애 시 기존 캐시 사용
- 품목명·품목코드·SKU·FNSKU·국가명 읽기 전용 표시
- 박스수량·무게(kg)·가로/세로/높이(cm) 직접 입력
- 한 박스에 여러 FNSKU와 개별 수량을 담는 합포
- 포장 당시 상품정보·상품DB 버전 스냅샷 저장
- 입력 변경 시 자동 임시저장 및 비정상 종료 복구
- Excel 포장실적 출력, 텍스트형 박스 라벨 출력·재출력
- F2/F4/F8/Ctrl+Enter 단축키와 스캔 후 자동 포커스
- 인증정보를 제외한 관리자 진단 ZIP 생성

## 폴더 구조

```text
src/beyondpack/
├─ app.py                 실행·의존성 조립
├─ ui.py                  PySide6 현장 화면
├─ cache.py               상품 SQLite 캐시·원자 교체
├─ sync.py                시작/수동 동기화
├─ packaging.py           합포·작업·감사·임시저장
├─ exporter.py            Excel 저장
├─ labels.py              박스 라벨
├─ token_cache.py         Windows DPAPI 토큰 보호
└─ sources/
   ├─ base.py             교체 가능한 ProductSource
   ├─ sharepoint.py       Microsoft Graph List 소스
   └─ json_source.py      개발·비상 배포용 JSON 소스
```

## 개발 환경 실행

Python 3.11 이상이 필요하다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
beyondpack --config config.demo.json
```

교육용 샘플 FNSKU는 `X003ABC123`, `X004DEF456`이다. `config.demo.json`은 운영에 사용하지 않는다.

## SharePoint 연결

상세한 목록 생성, 내부 컬럼, 권한, CSV와 대량 업로드 절차는 [`docs/SHAREPOINT_SETUP.md`](docs/SHAREPOINT_SETUP.md)를 따른다.

1. Entra ID에서 **Public client/native desktop app**을 등록한다.
2. 조직 정책에 맞춰 Microsoft Graph의 사이트 읽기 권한을 승인한다. 운영에서는 전역 사이트 권한보다 `Sites.Selected` 기반의 대상 사이트 한정 권한을 권장한다.
3. SharePoint에 `PLAN.MD` 6.2절의 내부명으로 `Product_Published` List를 만든다.
4. `config.example.json`을 `%LOCALAPPDATA%\BeyondPack\config.json`으로 복사한다.
5. `tenant_id`, `client_id`, `site_id`, `list_id`를 실제 값으로 변경한다.
6. 최초 실행 시 화면에 표시되는 Microsoft device-code 안내에 따라 한 번 로그인한다.

핵심 상품 필드는 SharePoint에서 직접 수정하지 않고 품목코드 관리 시스템에서 단방향 배포해야 한다. 포장정보는 SharePoint 상품 List에 쓰지 않는다.

## 상품 JSON 계약

SharePoint 연결 전 통합시험이나 비상 배포에는 다음 형태의 JSON을 사용할 수 있다.

```json
{
  "schema_version": 2,
  "data_version": "2026.08.11.01",
  "products": [
    {
      "FNSKU": "X003ABC123",
      "ItemCode": "A000000120",
      "SKU": "US-AMZ-SEAWEED-12P",
      "CountryCode": "US",
      "CountryName": "미국",
      "ProductName": "상품명",
      "Status": "Published",
      "SourceModifiedAt": "2026-08-11T00:00:00Z",
      "DataVersion": "2026.08.11.01",
      "SchemaVersion": 2,
      "LookupKey": "X003ABC123|US"
    }
  ]
}
```

## 자동 테스트

외부 패키지 없이 핵심 데이터 계층 테스트를 실행할 수 있다.

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## Windows 실행파일 다운로드·빌드

GitHub Releases의 `v2.1.0` 사전 릴리스는 다음 두 형식을 제공한다.

- `BeyondPack-2.1.0-Windows-x64-portable.zip`: 현장 권장본. 압축 해제 후 `BeyondPack\BeyondPack.exe` 실행
- `BeyondPack-2.1.0-Windows-x64.exe`: 단일 파일. 배포는 간단하지만 첫 실행이 더 느릴 수 있음

`SHA256SUMS.txt`로 파일 무결성을 확인한다. 현재 빌드는 코드서명과 현장 UAT 전이므로 운영 전 사내 코드서명·SmartScreen·백신·실제 장비 검증이 필요하다.

직접 빌드할 때는 다음 명령을 사용한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1 -OneFile
```

결과물은 각각 `dist\portable\BeyondPack\BeyondPack.exe`, `dist\onefile\BeyondPack.exe`에 생성된다. 운영 배포 전 회사 코드서명, 실제 라벨 규격, 프린터 드라이버, 백신 예외가 아닌 정상 평판 배포 절차를 검증한다.

## 아직 운영 정보가 필요한 항목

- Entra tenant/client ID와 SharePoint site/list ID
- 실제 `Product_Published` 테스트 데이터 50~100건
- 현재 v1.4 Excel 샘플과 라벨 실물/규격
- 현장 프린터 모델·DPI·용지 크기
- 작업자 식별 방식과 72시간 초과 캐시 승인권자
- 기존 Excel 불러오기 호환에 필요한 실제 파일 샘플

이 값들이 확정되기 전에는 “운영 배포 완료”로 간주하지 않는다.
