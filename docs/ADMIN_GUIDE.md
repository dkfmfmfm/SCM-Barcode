# BeyondPack 관리자 운영 가이드

## 시작 전 점검

- `%LOCALAPPDATA%\BeyondPack\config.json`의 SharePoint ID와 제한값 확인
- `data/products.db`와 `data/packaging.db` 쓰기 가능 여부 확인
- 상품DB 버전·건수·마지막 성공시간 확인
- 테스트 라벨 출력과 프린터 용지/DPI 확인
- 실제 FNSKU 정상·미등록·비활성 각 1건 확인

## 데이터 파일

| 파일 | 용도 |
|---|---|
| `products.db` | 현재 상품 캐시 |
| `products.previous.db` | 직전 정상 상품 캐시 |
| `packaging.db` | 작업·합포·감사·임시저장 |
| `sync-status.json` | 마지막 동기화 결과 |
| `msal-token.cache` | Windows DPAPI 보호 인증 캐시 |

상품 캐시 업데이트 실패 시 현재 `products.db`를 삭제하지 않는다. 데이터 급감이나 `FNSKU+국가` 복합키 중복 오류의 원인을 SharePoint 게시 데이터에서 먼저 수정한다.

## 진단

화면에서 `관리자 진단파일 생성`을 누르면 바탕화면에 ZIP이 생성된다. 이 파일은 앱/DB 버전, 파일 크기, DB 무결성, 동기화 상태만 포함하며 토큰과 상품/포장 원문은 포함하지 않는다.

## 배포 전 필수 UAT

- 단품 20건 연속 마우스 없이 처리
- 합포 2/5/10개 FNSKU 처리
- 인터넷 차단 후 기존 상품 조회
- 다운로드 도중 강제 종료 후 기존 DB 유지
- 프린터 오프라인 후 재출력
- 비정상 종료 후 미완료 입력 복구
- 실제 기존 Excel·라벨 결과와 대조
- 동일 FNSKU의 서로 다른 국가 2건이 각각 정확히 조회되는지 확인
- 박스 구성품 추가 후 국가 변경 차단 확인
