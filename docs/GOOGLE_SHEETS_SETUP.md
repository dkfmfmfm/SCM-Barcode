# Google Sheets 상품 마스터 설정

## 1. 시트 구성

기존 `product` 탭은 BeyondPack 1.4 호환을 위해 유지하고, 새 탭 이름을 `BeyondPack_Master`로 만든다. 첫 행에 다음 열을 정확히 입력한다.

```text
FNSKU,ItemCode,SKU,CountryCode,ProductName,ProductNameEn,AmazonAccount,Status,SourceModifiedAt,DataVersion,SchemaVersion
```

동일 FNSKU가 국가별로 존재하면 국가마다 한 행을 사용한다.

```text
X001ABC123 | US
X001ABC123 | CA
X001ABC123 | JP
```

동일한 `FNSKU+CountryCode` 조합은 두 번 등록할 수 없다. 화면의 국가는 `CountryCode`를 사용하므로 `CountryName` 열은 만들지 않는다. 기존 파일에 해당 열이 있어도 프로그램은 호환해서 읽는다. `SchemaVersion`은 현재 `2`다. `DataVersion`을 비우면 프로그램이 CSV 내용으로 자동 버전을 생성한다.

## 2. 접근 설정

BeyondPack은 브라우저 화면이나 Google API를 사용하지 않고 해당 탭을 CSV로 내려받는다. 따라서 포장 PC에서 로그인 없이 다운로드할 수 있는 공유 범위가 필요하다. 이 방식에는 원가·매출·개인정보를 넣지 않는다. FNSKU, 품목코드, SKU도 내부정보이므로 링크 전달 범위를 제한한다.

비공개 OAuth 연동이 필요해지면 공개 CSV 주소에 계정 기능을 얹지 말고 별도 인증형 `ProductSource`로 개발한다.

## 3. BeyondPack 연결

1. `BeyondPack_Master` 탭을 연다.
2. 주소창의 전체 Google Sheet URL을 복사한다.
3. BeyondPack 상단 `Sheet 설정`을 누른다.
4. URL을 붙여넣고 확인한다.
5. 상품 건수·버전·국가 목록을 확인한다.

이후에는 프로그램 실행 시 자동 다운로드되며 `F2`로 즉시 다시 받을 수 있다.

## 4. Excel 비상 파일

Google Sheet에서 `.xlsx`로 내려받거나 동일 열의 Excel을 준비한다. 시트 이름은 `BeyondPack_Master`를 권장한다. 현장에서는 `Excel 비상 업데이트`를 누르고 파일을 선택한다. 검증 실패 시 기존 DB는 변경되지 않는다.
