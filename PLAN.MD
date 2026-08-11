# BeyondPack 상품동기화·포장 프로그램 개발계획

> 기준 버전: 2.2.0  
> 확정일: 2026-08-11  
> 상시 상품 원본: Google Sheet  
> 현장 조회 원본: 로컬 SQLite  
> 비상 업데이트: Excel `.xlsx`

## 1. 최종 의사결정

BeyondPack은 평상시 Google Sheet의 `BeyondPack_Master` 탭을 CSV로 자동 다운로드한다. 다운로드한 데이터를 검증한 뒤에만 로컬 SQLite 상품DB를 교체한다. 작업 중 FNSKU 스캔은 네트워크가 아니라 로컬 DB를 조회한다. Google Sheet 또는 인터넷 장애 시 검증된 기존 DB로 작업을 계속하고, 관리자는 동일 스키마의 Excel을 비상 적용할 수 있다.

이 구조는 BeyondPack 1.4의 단순성과 속도를 유지하면서 다음 약점을 제거한다.

- EXE에 Google Sheet 주소 하드코딩
- 국가 구분 없는 FNSKU 조회
- 품목코드·SKU 미표시
- 무검증 CSV 적용
- 오프라인 캐시·이전 버전 부재
- 설치·바탕화면 바로가기 부재

## 2. 사업·현장 조건

| 조건 | 설계 반영 |
|---|---|
| 연 매출 약 1,000억 원 | 데이터 오류·중단·롤백 통제 필수 |
| 다품종·소량포장 | 빠른 FNSKU 조회와 신규상품 반영 |
| 단품과 합포 | 박스 하나에 복수 FNSKU·수량 저장 |
| 동일 FNSKU의 다국가 판매 | `FNSKU+CountryCode` 복합키 사용 |
| NAS 현장 연결 어려움 | NAS·사내웹 실시간 의존 금지 |
| 가벼운 프로그램 필요 | CSV는 시작 시만 받고 스캔은 SQLite 조회 |
| 향후 대형 창고 사용 | 상품소스·포장·WMS 코어 경계 분리 |

## 3. 범위

### 3.1 이번 버전 포함

- 실행 시 Google Sheet CSV 자동 다운로드
- F2 수동 즉시 업데이트
- Google Sheet URL 1회 설정과 로컬 저장
- Excel 상품 마스터 비상 업데이트
- 필수 열·필수값·중복·스키마·급감 검증
- SQLite 새 DB 생성·무결성 검사·원자 교체
- 직전 상품DB 한 세대 백업
- 국가 선택과 `FNSKU+CountryCode` 조회
- 품목명·품목코드·SKU·FNSKU·국가 표시
- 박스수량·무게·가로·세로·높이 직접 입력
- 박스당 상품수량과 합포
- 자동 임시저장, Excel 실적, 라벨·재출력
- 정식 설치파일, 바탕화면·시작 메뉴 아이콘
- 시작 로그와 관리자 진단파일

### 3.2 이번 버전 제외

- 재고 수불원장과 가용재고 계산
- PO·ASN·주문·Shipment 실행
- 다중 PC 실시간 박스번호 중앙조정
- 로케이션·피킹·패킹 태스크
- 로트·유통기한·일련번호
- SSCC·GS1-128 정식 물류라벨
- 저울·컨베이어·PLC 자동연동
- 고객사 판매용 라이선스와 자동결제

제외 기능을 단일 EXE에 계속 추가하지 않는다. 필요 시 중앙 WMS 서비스로 분리한다.

## 4. 기준 아키텍처

```text
상품 담당자
   │ 수정·승인
   ▼
Google Sheet / BeyondPack_Master
   │ 실행 시 또는 F2, CSV 다운로드
   ▼
GoogleSheetsProductSource
   │ 필수 열·복합키·버전·급감 검증
   ▼
products.new.db ── 무결성 성공 ──► products.db
                         │
                         └────────► products.previous.db

스캐너 ─► 국가+FNSKU 조회 ─► 로컬 products.db
                              │
                              ▼
                       포장정보 직접 입력
                              │
                              ▼
                    packaging.db / Excel / 라벨

비상 Excel ─► ExcelProductSource ─► 동일 검증·교체 절차
```

## 5. 상품 데이터 계약

### 5.1 열

| 열 | 필수 | 설명 |
|---|---:|---|
| `FNSKU` | Y | 스캔 식별자, 공백 제거·대문자 정규화 |
| `ItemCode` | Y | 사내 품목코드 |
| `SKU` | Y | 판매 SKU |
| `CountryCode` | Y | US, CA, JP, UK, DE 등 |
| `CountryName` | Y | 작업자 표시 국가 |
| `ProductName` | Y | 한글 품목명 |
| `ProductNameEn` | N | 영문 품목명 |
| `AmazonAccount` | N | 판매계정 |
| `Status` | 권장 | `Published`, `Active`, `Y`는 활성 |
| `SourceModifiedAt` | N | 원본 최종수정시각 |
| `DataVersion` | N | 비우면 CSV 내용 해시로 자동 생성 |
| `SchemaVersion` | N | 기본값 2 |
| `LookupKey` | N | 비우면 `FNSKU|CountryCode` 자동 생성 |

### 5.2 고유키

```text
normalized(FNSKU) + "|" + uppercase(CountryCode)
```

동일 FNSKU가 여러 국가에 존재하는 것은 정상이다. 동일 `FNSKU+CountryCode`가 두 행 이상이면 전체 업데이트를 거부한다.

### 5.3 상품과 포장정보 분리

Google Sheet에서 가져오는 값:

- 품목명, FNSKU, 품목코드, SKU, 국가

작업자가 매 작업마다 입력하는 값:

- 박스수량, 박스당 상품수량, 무게, 가로, 세로, 높이

포장정보를 상품 마스터에 저장하지 않는다.

## 6. 동기화 정책

### 6.1 자동 다운로드

1. 프로그램 창을 먼저 표시한다.
2. 별도 작업 스레드에서 해당 탭 CSV를 다운로드한다.
3. 연결 제한시간은 기본 10초다.
4. 최대 다운로드 크기는 기본 20MB다.
5. 검증 성공 후에만 로컬 DB를 교체한다.
6. F2 중복 클릭은 하나의 작업만 실행한다.

작업자가 CSV를 수동 다운로드하거나 파일명을 관리하지 않는다.

### 6.2 실패 처리

| 실패 | 처리 |
|---|---|
| 인터넷 없음·시간초과·HTTP 오류 | 기존 DB 유지 |
| Sheet 주소 없음 | 설정 안내, 기존 DB 유지 |
| 첫 행 열 제목 누락 | 누락 열 표시, 새 DB 미적용 |
| 동일 복합키 중복 | 전체 업데이트 거부 |
| 활성 상품 필수값 누락 | 전체 업데이트 거부 |
| 상품 수 20% 이상 급감 | 전체 업데이트 거부 |
| 스키마 불일치 | 전체 업데이트 거부 |
| 다운로드 중 종료 | 임시 DB 폐기, 기존 DB 유지 |

### 6.3 캐시 정책

- 캐시가 한 건도 없을 때만 스캔 작업을 차단한다.
- 오래된 캐시는 강하게 경고하지만 정상 DB라면 작업을 허용한다.
- 현장 작업중단보다 잘못된 신규 데이터 적용을 더 엄격하게 차단한다.
- `products.previous.db`로 직전 상품DB를 복구할 수 있어야 한다.

## 7. Excel 비상 업데이트

- 화면의 `Excel 비상 업데이트`에서 `.xlsx`를 선택한다.
- `BeyondPack_Master` 시트가 있으면 우선 사용하고, 없으면 첫 시트를 사용한다.
- Google Sheet와 동일한 열·검증·원자 교체 규칙을 적용한다.
- 적용 전 관리자 확인창을 표시한다.
- Excel 적용 후에도 Google Sheet 자동동기화 설정은 유지한다.
- 일반 작업자에게 상시 업무로 교육하지 않는다.

## 8. 작업자 UX

### 8.1 기본 흐름

```text
실행 → 상품DB 상태 확인 → 국가 선택 → FNSKU 스캔
→ 상품정보 대조 → EA/BOX 입력 → 합포 구성 추가
→ 포장정보 입력 → Ctrl+Enter 확정 → 라벨 출력
```

### 8.2 화면 표시 우선순위

1. 품목명
2. 품목코드
3. SKU
4. FNSKU
5. 국가

### 8.3 편의성 기준

- 스캔 후 상품수량으로 포커스 자동 이동
- 합포 추가 후 다음 FNSKU 입력으로 복귀
- 박스에 상품이 있으면 국가 변경 차단
- 성공·경고·오류를 색상뿐 아니라 문장과 소리로 구분
- 오류 메시지에 원인과 다음 행동 포함
- F2, F4, F8, Ctrl+Enter 단축키 유지
- 강제 종료 후 마지막 미완료 작업 복구

## 9. 로컬 데이터

```text
%LOCALAPPDATA%\BeyondPack\config.json
%LOCALAPPDATA%\BeyondPack\data\products.db
%LOCALAPPDATA%\BeyondPack\data\products.previous.db
%LOCALAPPDATA%\BeyondPack\data\packaging.db
%LOCALAPPDATA%\BeyondPack\data\sync-status.json
%LOCALAPPDATA%\BeyondPack\logs\startup.log
```

앱 설치·업데이트·제거와 사용자 데이터를 분리한다. 설치 제거가 상품DB와 포장기록을 삭제하면 안 된다.

## 10. 성능·안정성 기준

| 지표 | 목표 |
|---|---:|
| 로컬 조회 p95 | 100ms 이하 |
| 연속 스캔 중 UI 정지 1초 이상 | 0건 |
| 동일 복합키 허용 | 0건 |
| 잘못된 업데이트로 기존 DB 손상 | 0건 |
| 캐시 상태 인터넷 차단 조회 | 100% |
| 포장 저장 시 상품 스냅샷 일치 | 100% |
| 비정상 종료 후 미완료 작업 복구 | 100% |

## 11. 보안 기준

- Google Sheet 주소는 EXE에 하드코딩하지 않는다.
- 원가·매출·개인정보·인증정보를 상품 마스터에 넣지 않는다.
- BeyondPack은 Google Sheet 쓰기·삭제 기능을 갖지 않는다.
- 진단 ZIP에 상품 원문과 포장 원문을 넣지 않는다.
- 공개 링크 위험이 허용되지 않으면 OAuth/API 소스를 별도 개발한다.
- 향후 상업 배포는 회사 코드서명과 자동 업데이트 서명을 적용한다.

## 12. Windows 설치·배포

권장 배포물:

```text
BeyondPack-2.2.0-Windows-x64-Setup.exe
```

설치 프로그램은 다음을 제공한다.

- 관리자 권한 없는 현재 사용자 설치
- 바탕화면 BeyondPack 아이콘 기본 생성
- 시작 메뉴 바로가기
- 프로그램 아이콘과 제거 프로그램
- 기존 `%LOCALAPPDATA%\BeyondPack` 데이터 보존

포터블 ZIP과 단일 EXE는 설치 제한·진단용으로 계속 제공한다.

## 13. 시험계획

### 13.1 자동시험

- Google Sheet URL→CSV URL 변환
- CSV 자동 다운로드와 매핑
- 기존 2열 시트 적용 거부
- 동일 FNSKU·다국가 허용
- 동일 복합키 중복 전체 거부
- 필수값·스키마·상품 급감 거부
- SQLite 원자 교체와 이전 DB 보관
- Excel 동일 스키마 가져오기
- SharePoint 구설정의 Google Sheet 설정 마이그레이션
- GUI 실제 창 생성 self-test

### 13.2 현장 UAT

- 단품 20건 연속 작업
- 2·5·10개 FNSKU 합포
- 동일 FNSKU의 두 국가 조회
- 신규상품 Sheet 수정 후 재실행 자동 반영
- 실행 중 Sheet 수정 후 F2 반영
- 인터넷 차단·복구
- 열 누락·중복·상품 급감 파일 적용 차단
- Excel 비상 업데이트
- 프린터 오프라인 후 재출력
- 강제 종료 후 작업 복구
- 설치·업데이트·제거 후 데이터 보존

## 14. 단계 배포

### Phase 0 — 데이터 준비

- [ ] 기존 `product` 탭 유지
- [ ] `BeyondPack_Master` 탭 생성
- [ ] 복합키 기준 데이터 업로드
- [ ] 편집자·승인자·작업자 역할 지정
- [ ] 원가·매출·개인정보 제외 확인

### Phase 1 — 1대 파일럿

- [ ] 설치파일과 바탕화면 아이콘 확인
- [ ] Google Sheet 주소 1회 설정
- [ ] 실제 스캐너·프린터 시험
- [ ] 1주간 미등록·오류·처리시간 기록

### Phase 2 — 순차 확대

- [ ] 1대 → 3~5대 → 전체 PC
- [ ] v1.4와 직전 DB 롤백본 보관
- [ ] 첫 1개월 주간 데이터 품질 검토
- [ ] 작업자·관리자 SOP 배포

### Phase 3 — 중앙 WMS 판단

다음 조건이 나타나면 데스크톱 기능 누적을 멈추고 WMS 코어를 도입한다.

- 여러 작업대의 실시간 재고·박스번호 충돌
- PO·ASN·주문·Shipment 추적 필요
- 로케이션·피킹웨이브·보충 태스크 필요
- 로트·유통기한·일련번호 추적 필요
- 고객사별 재고와 SLA 분리 필요
- 자동화설비·EDI·택배 API 필요

## 15. 대형 창고 확장 경계

```text
현재: GoogleSheetsProductSource → Local Product Cache → Scan/Packing UI
향후: ERP/WMS Product API       → Local Product Cache → Scan/Packing UI
                                   │
                                   └→ WMS Task/LPN/Inventory Service
```

화면과 스캐너 입력은 유지할 수 있지만 다음은 중앙 서비스가 소유해야 한다.

- 불변 재고이동 원장
- 로케이션·LPN·팔레트·SSCC
- 주문·Shipment·ASN
- 다중 작업대 동시성·멱등성
- 역할·감사·다고객·다창고
- 중앙 Print Job과 라벨 템플릿 버전

## 16. 상업화 기준

| 영역 | 기준 |
|---|---|
| 설치 | 서명된 설치파일, 바탕화면 아이콘, 안전한 제거 |
| 고객분리 | 고객별 Sheet·DB·설정·로그 완전 분리 |
| 설정화 | 국가·단위·라벨·프린터를 코드수정 없이 설정 |
| 데이터 이동성 | 상품·포장실적 표준 형식 내보내기 |
| 지원 | 오류코드·진단파일·원격지원 SOP |
| 호환성 | 지원 Windows·스캐너·프린터 목록 |
| 보안 | 고객별 링크·토큰·인증서 분리와 교체 |
| 계약 | 데이터소유권·백업책임·SLA·지원범위 |
| 확장 | Google Sheet 외 ERP/WMS 어댑터 지원 |

내부 파일럿에서 작업시간·오류율·교육시간 개선을 수치로 입증한 뒤 Starter/Professional/Enterprise 상품화를 검토한다.

## 17. 최종 인수기준

1. 실행 시 CSV가 자동 다운로드되고 작업자는 파일을 저장하지 않는다.
2. Google Sheet 오류가 기존 로컬 DB를 손상시키지 않는다.
3. 동일 FNSKU가 국가별로 정확히 조회된다.
4. 품목코드·SKU·국가·품목명이 화면과 Excel에 일치한다.
5. 신규 작업자가 30분 교육 후 단품과 합포를 처리한다.
6. 네트워크 단절 중에도 검증된 캐시 상품을 포장한다.
7. Excel 비상 업데이트 실패 시 기존 DB를 유지한다.
8. 설치 후 바탕화면 아이콘으로 실행된다.
9. 업데이트·제거 과정에서 상품DB와 포장기록이 보존된다.
10. Windows CI에서 단위시험·실제 GUI 시작·포터블·단일 EXE·설치파일 빌드가 통과한다.

이 문서와 `RESEARCH.MD`가 BeyondPack 구현·검토·인수의 기준이다.
