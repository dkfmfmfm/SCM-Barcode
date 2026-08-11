# BeyondPack 2.1.1 (시작 진단 수정)

Windows에서 실행 버튼을 눌러도 화면이나 오류 안내가 나타나지 않는 문제를 진단하고 재발을 방지하기 위한 수정 릴리스입니다.

## 변경 사항

- 실제 Qt GUI 창을 생성하는 Windows 실행파일 self-test 추가
- 시작·종료·치명적 오류를 `%LOCALAPPDATA%\BeyondPack\logs\startup.log`에 기록
- GUI 초기화 전에 실패하더라도 Windows 기본 오류창으로 원인과 로그 위치 표시
- PySide6 또는 필수 화면 구성요소 누락을 조용히 종료하지 않고 명확히 안내
- 기존 상품 캐시와 포장실적은 그대로 유지

## 다운로드 선택

- `BeyondPack-2.1.1-Windows-x64-portable.zip`: 현장 권장본
- `BeyondPack-2.1.1-Windows-x64.exe`: 단일 파일
- `SHA256SUMS.txt`: 다운로드 파일 무결성 확인용

현재도 코드서명 및 실제 스캐너·프린터 UAT 전 단계입니다. Windows SmartScreen이나 회사 보안제품이 차단한 경우에는 우회하지 말고 사내 보안 담당자의 승인 및 코드서명 절차를 진행하십시오.
