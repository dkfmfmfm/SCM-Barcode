# BeyondPack 2.2.11

`PLAN.MD`와 `RESEARCH.MD`를 기준으로 구현한 Windows 포장 작업 프로그램이다. 평상시 Google Sheet를 CSV로 자동 다운로드하고, 검증에 성공한 상품만 로컬 SQLite에 저장한다. 실제 스캔 조회는 네트워크가 아니라 `FNSKU+CountryCode` 로컬 인덱스를 사용한다.

## 운영 원칙

- 기본 상품 원본: Google Sheet의 `BeyondPack` 탭
- 자동 업데이트: 프로그램 시작 시, 또는 `F2` 실행 시 CSV 자동 다운로드
- 현장 조회: 활성 로컬 상품DB 스냅샷에서 즉시 조회
- 장애 대응: 다운로드·형식·중복 오류 시 기존 DB 유지
- 비상 업데이트: 관리자가 `.xlsx` 파일을 직접 선택해 동일 검증 후 반영
- 포장정보: 출고건·박스수량·무게·가로·세로·높이·박스당 상품수량은 작업자가 입력

## 주요 기능

- 동일 FNSKU의 국가별 상품 허용, 동일 `FNSKU+국가` 중복 차단
- 품목명·품목코드·SKU·FNSKU·국가 읽기 전용 표시
- 한 박스에 여러 FNSKU와 수량을 담는 합포
- 상품 수 20% 이상 급감, 필수값 누락, 스키마 불일치 시 전체 업데이트 차단
- 잠긴 기존 DB를 덮어쓰지 않는 불변 스냅샷과 활성 포인터 방식, 직전 DB 자동 보존
- 모든 숫자 입력칸에 현장용 대형 `▲/▼` 증감 버튼 제공
- 인터넷 장애와 오래된 캐시 경고 중에도 기존 검증 DB로 작업 가능
- 포장 당시 상품정보·상품DB 버전 스냅샷 저장
- 자동 임시저장과 비정상 종료 복구
- 출고건 단위로 이어지는 박스번호. 재시작해도 같은 출고건이면 이어서 매기고 다른 출고건이면 `#1`부터 시작
- 박스수량만큼 `#1`, `#2` 순번 라벨을 한 번의 인쇄 작업으로 출력(기본 40×25mm)
- 라벨 프린터·라벨 크기(mm)·자동출력을 화면에서 설정, 테스트 라벨 출력
- 현황 표와 라벨 모두 스캔한 FNSKU 표시, 출고건 작업 현황 실시간 누적과 지난 박스 재출력
- 마지막 박스 수정·삭제. 사유를 남기고 박스번호를 되돌린다
- 출고건 단위 Excel 포장실적
- 포장 실적 자동 백업: 공유 폴더에 DB 사본과 일자별 CSV를 작업대별 폴더로 저장
- 상품 마스터 자동 백업: 상품이 바뀔 때마다 `Excel 비상 업데이트`로 되돌릴 수 있는 `.xlsx` 사본 보관
- 정식 설치파일, 프로그램/바탕화면/시작 메뉴 아이콘
- 시작 로그와 관리자 진단 ZIP

## 상품 마스터 열

첫 행은 다음 내부명을 사용한다. `LookupKey`가 없으면 프로그램이 `FNSKU|CountryCode`로 생성한다. `DataVersion`이 없으면 CSV 내용 해시로 자동 생성한다.

```text
FNSKU
ItemCode
SKU
CountryCode
ProductName
ProductNameEn
AmazonAccount
Status
SourceModifiedAt
DataVersion
SchemaVersion
```

필수 열은 `FNSKU`, `ItemCode`, `SKU`, `CountryCode`, `ProductName`이다. `CountryName`은 이전 파일 호환용 선택 열이며, 없으면 `CountryCode`가 화면의 국가로 표시된다. 활성 상태는 `Published`, `Active`, `Y`를 사용할 수 있다.

## 최초 설정

1. Google Sheet의 `BeyondPack` 탭 첫 행에 위 열을 넣는다.
2. 설치 후 바탕화면의 `BeyondPack`을 실행한다.
3. 상단 `Sheet 설정`을 누르고 해당 탭이 열린 Google Sheet 주소를 붙여넣는다. 프로그램이 CSV를 자동 다운로드하고 검증한 뒤 국가 목록을 표시한다.
4. **Windows 프린터 드라이버에 라벨 크기(기본 40×25mm)를 먼저 등록한다.** 등록하지 않으면 드라이버가 크기 지정을 거부해 A4로 인쇄된다.
5. `설정·관리자 도구 > 라벨 설정`에서 프린터와 크기를 지정한다. 안내줄이 초록인지 확인하고 `테스트 라벨 출력`으로 검증한다.
6. `설정·관리자 도구 > 자동 백업 설정`에서 공유 폴더를 지정한다. 지정하지 않으면 포장 실적이 이 PC에만 남고 상품 마스터 사본도 만들어지지 않는다.

| 문서 | 대상 |
|---|---|
| [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md) | 설치·화면·작업 흐름·박스번호·라벨·오류대응 전체 |
| [`docs/FIELD_CHECKLIST.md`](docs/FIELD_CHECKLIST.md) | **설치 후 현장 도입 점검표** — 인쇄해서 항목별로 확인 |
| [`docs/OPERATOR_GUIDE.md`](docs/OPERATOR_GUIDE.md) | 현장 작업자 1페이지 요약 |
| [`docs/ADMIN_GUIDE.md`](docs/ADMIN_GUIDE.md) | PC 세팅·상품 업데이트·실적 보관·UAT |
| [`docs/GOOGLE_SHEETS_SETUP.md`](docs/GOOGLE_SHEETS_SETUP.md) | 상품 마스터 작성 기준 |

## 데이터 위치

```text
%LOCALAPPDATA%\BeyondPack\config.json
%LOCALAPPDATA%\BeyondPack\data\products.current.json
%LOCALAPPDATA%\BeyondPack\data\products.snapshot.<PID>.<UUID>.db
%LOCALAPPDATA%\BeyondPack\data\packaging.db
%LOCALAPPDATA%\BeyondPack\logs\startup.log
```

`자동 백업 설정`의 위치에 다음이 함께 복사된다.

```text
<백업 위치>\<PC이름>\packaging.db              포장기록 전체 사본
<백업 위치>\<PC이름>\packing-YYYYMMDD.csv      그날 확정한 박스 실적
<백업 위치>\products\products-날짜-버전.xlsx   상품 마스터 사본
```

상품 마스터 사본은 상품 내용이 바뀔 때만 새로 쌓인다. Google Sheet를 잃어도 이 파일을 `Excel 비상 업데이트`로 그대로 되돌릴 수 있다.

프로그램 업데이트·제거 시에도 위 사용자 데이터는 유지한다.

## 개발과 테스트

Python 3.11 이상이 필요하다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
beyondpack --config config.demo.json
```

## Windows 배포본

- `BeyondPack-<버전>-Windows-x64-Setup.exe`: 권장. 설치와 바탕화면 아이콘 자동 생성
- `BeyondPack-<버전>-Windows-x64-portable.zip`: 설치가 제한된 PC용
- `BeyondPack-<버전>-Windows-x64.exe`: 단일 실행파일
- `SHA256SUMS.txt`: 다운로드 무결성 확인

현재 빌드는 회사 코드서명과 실제 스캐너·프린터 UAT 전이므로, 전체 배포 전에 포장 PC 1대에서 단품·합포·인터넷 차단·라벨 회귀시험을 수행한다.
