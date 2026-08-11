# BeyondPack 관리자 운영 가이드

## 시작 전 점검

- `Sheet 설정`에 `BeyondPack` 탭 주소가 저장되어 있는지 확인
- 상단에 상품DB 버전·건수·마지막 성공시간이 표시되는지 확인
- 정상·미등록·비활성 FNSKU 각 1건 확인
- 테스트 라벨과 프린터 용지/DPI 확인

## 상품 업데이트

평상시에는 프로그램 시작 시 Google Sheet CSV가 자동 다운로드된다. 실행 중 즉시 반영해야 하면 `F2`를 누른다. 작업자는 CSV를 따로 다운로드하지 않는다.

업데이트 순서는 `다운로드 → 필수 열 검사 → FNSKU+국가 중복 검사 → 필수값·버전 검사 → 새 SQLite 무결성 검사 → 기존 DB 백업 → 원자 교체`다. 어느 단계에서든 실패하면 기존 `products.db`를 그대로 사용한다.

Google Sheet 장애 시 `Excel 비상 업데이트`로 `.xlsx`를 선택한다. `BeyondPack` 시트를 우선 사용하며 기존 `BeyondPack_Master`도 호환한다. 둘 다 없으면 첫 시트를 사용한다. Excel도 Google Sheet와 동일한 열과 검증 규칙을 따른다.

## 데이터 파일

| 파일 | 용도 |
|---|---|
| `products.db` | 현재 검증된 상품 캐시 |
| `products.previous.db` | 직전 정상 상품 캐시 |
| `packaging.db` | 작업·합포·감사·임시저장 |
| `sync-status.json` | 마지막 업데이트 결과 |
| `config.json` | Google Sheet 주소와 제한값 |

기본 위치는 `%LOCALAPPDATA%\BeyondPack\data`다. 오래된 DB는 경고하지만 캐시가 정상이라면 포장 작업을 막지 않는다. 캐시가 한 건도 없을 때만 작업을 차단한다.

## 진단과 UAT

`관리자 진단파일 생성`은 바탕화면에 ZIP을 만든다. 상품 원문과 포장 원문은 포함하지 않는다.

- 단품 20건 연속 처리
- 합포 2/5/10개 FNSKU 처리
- 동일 FNSKU·다른 국가 조회
- 인터넷 차단 후 기존 DB 조회
- 잘못된 CSV/Excel 적용 차단과 기존 DB 유지
- 프린터 오프라인 후 재출력
- 강제 종료 후 미완료 입력 복구
