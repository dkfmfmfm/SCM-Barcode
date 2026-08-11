[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SiteUrl,

    [Parameter(Mandatory = $true)]
    [string]$ClientId,

    [string]$ListName = "Product_Published",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Get-Module -ListAvailable -Name PnP.PowerShell)) {
    throw "PnP.PowerShell이 없습니다. PowerShell 7.4 이상에서 Install-Module PnP.PowerShell -Scope CurrentUser를 실행하세요."
}

$fieldDefinitions = @(
    @{ Internal = "FNSKU";           Display = "FNSKU";         Type = "Text";     Required = $true;  Choices = $null },
    @{ Internal = "ItemCode";        Display = "품목코드";       Type = "Text";     Required = $true;  Choices = $null },
    @{ Internal = "SKU";             Display = "SKU";           Type = "Text";     Required = $true;  Choices = $null },
    @{ Internal = "CountryCode";     Display = "국가코드";       Type = "Text";     Required = $true;  Choices = $null },
    @{ Internal = "LookupKey";       Display = "조회키";         Type = "Text";     Required = $true;  Choices = $null },
    @{ Internal = "CountryName";     Display = "국가명";         Type = "Text";     Required = $true;  Choices = $null },
    @{ Internal = "ProductName";     Display = "품목명";         Type = "Text";     Required = $true;  Choices = $null },
    @{ Internal = "ProductNameEn";   Display = "영문 품목명";    Type = "Text";     Required = $false; Choices = $null },
    @{ Internal = "AmazonAccount";   Display = "Amazon 계정";   Type = "Text";     Required = $false; Choices = $null },
    @{ Internal = "Status";          Display = "상태";           Type = "Choice";   Required = $true;  Choices = @("Published", "Inactive") },
    @{ Internal = "SourceModifiedAt";Display = "원본 수정시각";  Type = "DateTime"; Required = $true;  Choices = $null },
    @{ Internal = "DataVersion";     Display = "데이터 버전";    Type = "Text";     Required = $true;  Choices = $null },
    @{ Internal = "SchemaVersion";   Display = "스키마 버전";    Type = "Number";   Required = $true;  Choices = $null }
)

Write-Host "생성 대상: $SiteUrl / $ListName"
Write-Host "열: $($fieldDefinitions.Internal -join ', ')"
if (-not $Apply) {
    Write-Host "DRY RUN입니다. SharePoint는 변경하지 않았습니다. 실제 생성은 -Apply를 추가하세요."
    return
}

Import-Module PnP.PowerShell
Connect-PnPOnline -Url $SiteUrl -Interactive -ClientId $ClientId

$list = Get-PnPList -Identity $ListName -ErrorAction SilentlyContinue
if ($null -eq $list) {
    New-PnPList -Title $ListName -Url "Lists/$ListName" -Template GenericList -EnableVersioning | Out-Null
    Write-Host "목록 생성: $ListName"
} else {
    Write-Host "기존 목록을 사용합니다: $ListName"
}

Set-PnPField -List $ListName -Identity "Title" -Values @{ Required = $false; Title = "관리키" } | Out-Null

foreach ($definition in $fieldDefinitions) {
    $field = Get-PnPField -List $ListName -Identity $definition.Internal -ErrorAction SilentlyContinue
    if ($null -eq $field) {
        $parameters = @{
            List          = $ListName
            InternalName  = $definition.Internal
            DisplayName   = $definition.Display
            Type          = $definition.Type
            AddToDefaultView = $true
        }
        if ($definition.Required) { $parameters["Required"] = $true }
        if ($null -ne $definition.Choices) { $parameters["Choices"] = $definition.Choices }
        Add-PnPField @parameters | Out-Null
        Write-Host "열 생성: $($definition.Internal)"
    } else {
        if ($field.TypeAsString -ne $definition.Type) {
            throw "$($definition.Internal) 열 형식이 다릅니다. 현재 $($field.TypeAsString), 필요 $($definition.Type)"
        }
        Set-PnPField -List $ListName -Identity $definition.Internal -Values @{
            Title = $definition.Display
            Required = $definition.Required
        } | Out-Null
    }
}

Set-PnPField -List $ListName -Identity "FNSKU" -Values @{
    Indexed = $true
    EnforceUniqueValues = $false
} | Out-Null

Set-PnPField -List $ListName -Identity "LookupKey" -Values @{
    Indexed = $true
    EnforceUniqueValues = $true
} | Out-Null

foreach ($fieldName in @("CountryCode", "Status", "DataVersion", "SourceModifiedAt")) {
    Set-PnPField -List $ListName -Identity $fieldName -Values @{ Indexed = $true } | Out-Null
}

Set-PnPField -List $ListName -Identity "SchemaVersion" -Values @{ Decimals = 0 } | Out-Null

$viewFields = @("FNSKU", "CountryCode", "LookupKey", "ItemCode", "SKU", "CountryName", "ProductName", "Status", "DataVersion", "SourceModifiedAt")
$publishedView = Get-PnPView -List $ListName -Identity "Published 상품" -ErrorAction SilentlyContinue
if ($null -eq $publishedView) {
    $query = "<Where><Eq><FieldRef Name='Status'/><Value Type='Choice'>Published</Value></Eq></Where><OrderBy><FieldRef Name='FNSKU' Ascending='TRUE'/></OrderBy>"
    Add-PnPView -List $ListName -Title "Published 상품" -Fields $viewFields -Query $query -Paged -RowLimit 100 | Out-Null
}

Write-Host "완료: Product_Published 목록, FNSKU+국가 LookupKey 고유값, 인덱스, Published 보기를 구성했습니다."
