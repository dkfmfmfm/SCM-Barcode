# SharePoint `Product_Published` 구축·대량 업로드 가이드

## 1. 권장 구조

개인용 `내 목록(My Lists)`이 아니라 회사 SharePoint 사이트 안에 빈 목록을 만든다.

```text
품목코드 관리 시스템
  → 검증된 Amazon 상품만 단방향 배포
SharePoint / Product_Published
  → BeyondPack 실행 시 전체 다운로드
작업 PC / products.db
  → 작업 중 FNSKU 로컬 조회
```

목록 이름은 `Product_Published`로 고정한다. SharePoint는 상품정보의 배포 지점이며 포장정보·무게·치수·작업실적을 저장하지 않는다.

## 2. 목록 만들기

가장 안전한 방법은 저장소의 생성 스크립트를 먼저 dry run한 뒤 실행하는 것이다.

```powershell
pwsh .\scripts\create-sharepoint-list.ps1 `
  -SiteUrl "https://회사명.sharepoint.com/sites/SCM" `
  -ClientId "ENTRA-PUBLIC-CLIENT-ID"

pwsh .\scripts\create-sharepoint-list.ps1 `
  -SiteUrl "https://회사명.sharepoint.com/sites/SCM" `
  -ClientId "ENTRA-PUBLIC-CLIENT-ID" `
  -Apply
```

수동으로 만들려면 다음 순서를 따른다.

1. 상품정보를 관리할 SharePoint 팀 사이트로 이동한다.
2. `사이트 콘텐츠` → `새로 만들기` → `목록` → `빈 목록`을 선택한다.
3. 이름을 `Product_Published`로 입력하고 해당 사이트에 저장한다.
4. 기본 `제목(Title)` 열은 삭제하지 말고 필수값을 해제한다. 업로드 스크립트는 호환성을 위해 Title에도 FNSKU를 넣는다.
5. 아래 열을 **영문 내부명으로 먼저 생성**한다. 필요하면 생성 후 표시 이름만 한국어로 바꾼다.

> SharePoint 내부명은 열 생성 후 표시 이름을 바꿔도 그대로 유지된다. 처음부터 한글 또는 공백이 포함된 이름으로 만들면 Graph API 필드명이 달라질 수 있다.

| 생성할 내부명 | 권장 표시명 | 형식 | 필수 | 설정 |
|---|---|---|:---:|---|
| `FNSKU` | FNSKU | 한 줄 텍스트 | Y | 중복 허용, 인덱스 |
| `ItemCode` | 품목코드 | 한 줄 텍스트 | Y |  |
| `SKU` | SKU | 한 줄 텍스트 | Y |  |
| `CountryCode` | 국가코드 | 한 줄 텍스트 | Y | US/JP/DE 등 |
| `LookupKey` | 조회키 | 한 줄 텍스트 | Y | `FNSKU|CountryCode`, 고유값 적용 |
| `CountryName` | 국가명 | 한 줄 텍스트 | Y | 미국/일본/독일 등 |
| `ProductName` | 품목명 | 한 줄 텍스트 | Y |  |
| `ProductNameEn` | 영문 품목명 | 한 줄 텍스트 | N |  |
| `AmazonAccount` | Amazon 계정 | 한 줄 텍스트 | N | 기본 화면에서 숨김 |
| `Status` | 상태 | 선택 | Y | `Published`, `Inactive` |
| `SourceModifiedAt` | 원본 수정시각 | 날짜 및 시간 | Y | 날짜+시간 표시 |
| `DataVersion` | 데이터 버전 | 한 줄 텍스트 | Y | 한 배포 내 동일값 |
| `SchemaVersion` | 스키마 버전 | 숫자 | Y | 소수 0자리, 현재 `2` |

6. `FNSKU` 열은 `고유 값 적용: 아니요`, `인덱스: 예`로 설정한다.
7. `LookupKey` 열은 **계산 열이 아닌 한 줄 텍스트**로 만들고 `고유 값 적용: 예`로 설정한다.
8. 목록 설정의 `인덱싱된 열`에서 `CountryCode`, `Status`, `DataVersion`, `SourceModifiedAt`을 추가한다.
9. `버전 관리 설정`에서 항목을 수정할 때마다 버전을 생성하도록 한다.
10. 기본 보기에는 FNSKU·국가코드·LookupKey·품목코드·SKU·국가명·품목명·상태·데이터버전을 표시한다.
11. `Published 상품` 보기를 추가하고 `Status = Published` 필터를 적용한다.

## 3. 권한

| 주체 | 권한 |
|---|---|
| 품목관리 배포 담당자 | 항목 추가·수정 |
| 승인 관리자 | 상태 확인·버전 복원 |
| BeyondPack 앱/작업자 계정 | 읽기 전용 |
| 일반 포장 작업자 | SharePoint 직접 편집 권한 없음 |

초기 MVP 코드는 Microsoft 로그인 기반의 위임된 읽기 권한을 사용한다. Client Secret을 EXE나 `config.json`에 넣지 않는다. 운영 앱 등록과 권한 승인은 Microsoft 365 관리자가 수행한다.

## 4. 업로드 CSV

CSV 첫 줄은 아래 내부명과 정확히 일치해야 한다.

```csv
FNSKU,ItemCode,SKU,CountryCode,CountryName,ProductName,ProductNameEn,AmazonAccount,Status,SourceModifiedAt,DataVersion,SchemaVersion,LookupKey
X003ABC123,A000000120,US-AMZ-SEAWEED-12P,US,미국,DONGWON YANGBAN SEAWEED 12 PACK,DONGWON YANGBAN SEAWEED 12 PACK,US-01,Published,2026-08-11T00:00:00Z,2026.08.11.02,2,X003ABC123|US
```

필수 규칙:

- FNSKU는 공백을 제거하고 대문자로 통일한다.
- 동일 FNSKU가 국가별로 반복되는 것은 허용한다.
- CSV 안의 `FNSKU|CountryCode` 복합키 중복은 0건이어야 한다.
- `LookupKey`는 정규화된 `FNSKU|CountryCode`와 정확히 같아야 한다.
- `Published` 행은 ItemCode, SKU, CountryCode, CountryName, ProductName이 모두 있어야 한다.
- 한 번의 배포에는 DataVersion 하나만 사용한다.
- SourceModifiedAt은 ISO 8601 날짜·시간으로 넣는다.
- Excel에서 CSV UTF-8 형식으로 저장해 한글 깨짐을 방지한다.

## 5. 대량 업로드 방법

### A. 소량·1회: 그리드 보기 붙여넣기

수십~수백 건을 한 번 넣거나 일부 값을 고칠 때 적합하다.

1. `그리드 보기에서 편집`을 누른다.
2. Excel에서 열 순서를 SharePoint 보기와 동일하게 맞춘다.
3. 첫 번째 빈 행의 첫 셀을 선택한다.
4. Excel 범위를 복사해 붙여넣는다.
5. 저장이 끝난 뒤 그리드 보기를 종료한다.
6. 총 건수, 중복, 빈 필수값과 DataVersion을 확인한다.

### B. 새 목록 1회 생성: Excel/CSV에서 목록 만들기

Microsoft Lists는 Excel 표 또는 CSV에서 새 목록을 만들 수 있다. 다만 자동 생성된 내부명이 BeyondPack 계약과 달라질 수 있으므로 **운영 `Product_Published`에는 권장하지 않는다.** 테스트 목록이나 필드 초안을 만들 때만 사용한다.

### C. 운영 권장: PnP PowerShell 배치 업서트

수천~수만 건의 최초 등록, 정기 전체 배포, 기존 행 수정에 적합하다. 저장소의 `scripts/import-sharepoint-products.ps1`는 다음을 수행한다.

- 업로드 전 필수값·복합키 중복·스키마·데이터버전 전체 검증
- 기본 실행은 SharePoint를 바꾸지 않는 dry run
- LookupKey가 있으면 수정, 없으면 신규 등록
- 100건 단위 기본 배치 처리
- 선택적으로 CSV에서 사라진 기존 상품을 `Inactive`로 변경

PowerShell 7.4 이상에서 준비:

```powershell
Install-Module PnP.PowerShell -Scope CurrentUser
```

먼저 검증만 수행:

```powershell
pwsh .\scripts\import-sharepoint-products.ps1 `
  -SiteUrl "https://회사명.sharepoint.com/sites/SCM" `
  -ClientId "ENTRA-PUBLIC-CLIENT-ID" `
  -CsvPath ".\products.csv"
```

오류가 없을 때 실제 반영:

```powershell
pwsh .\scripts\import-sharepoint-products.ps1 `
  -SiteUrl "https://회사명.sharepoint.com/sites/SCM" `
  -ClientId "ENTRA-PUBLIC-CLIENT-ID" `
  -CsvPath ".\products.csv" `
  -Apply
```

CSV에 없는 기존 상품까지 비활성화할 때만 명시적으로 추가한다.

```powershell
  -Apply -DeactivateMissing
```

`-DeactivateMissing`은 CSV가 전체 상품 스냅샷일 때만 사용한다. 일부 국가·브랜드 파일에 사용하면 정상 상품까지 비활성화될 수 있다.

## 6. 업로드 후 검증

1. SharePoint 총 행 수와 원본 게시 건수가 일치한다.
2. LookupKey 고유값 위반이 없다.
3. `Published` 필수값 누락이 0건이다.
4. DataVersion이 한 개다.
5. BeyondPack에서 `상품정보 업데이트`를 실행한다.
6. 정상 FNSKU 10건, 같은 FNSKU의 국가별 상품, 소문자·공백 FNSKU, 미등록·비활성 FNSKU를 시험한다.
7. 상품DB 버전·건수·마지막 성공시간을 기록한다.
8. 문제가 있으면 SharePoint 데이터를 수정하고 새 DataVersion으로 다시 배포한다.

## 7. 운영 원칙

- 전체 삭제 후 재등록하지 않고 LookupKey 기준 업서트를 사용한다.
- 데이터가 20% 이상 줄면 BeyondPack 적용이 거부되므로 원인을 먼저 확인한다.
- 품목관리 시스템과 SharePoint 양쪽에서 핵심 상품 필드를 수정하지 않는다.
- 포장 작업 시작 직전에 대규모 배포하지 않는다.
- 배포 파일, 건수, DataVersion, 실행자, 결과를 남긴다.
- 운영 안정화 후 품목관리 시스템에서 동일 검증을 거쳐 자동 배포하도록 전환한다.
