[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SiteUrl,

    [Parameter(Mandatory = $true)]
    [string]$ClientId,

    [Parameter(Mandatory = $true)]
    [string]$CsvPath,

    [string]$ListName = "Product_Published",
    [string]$DataVersion = "",
    [ValidateRange(1, 500)]
    [int]$BatchSize = 100,
    [switch]$DeactivateMissing,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Normalize-Fnsku {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return "" }
    return (($Value.ToString() -replace "\s", "").Trim().ToUpperInvariant())
}

function Normalize-CountryCode {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return "" }
    return (($Value.ToString() -replace "\s", "").Trim().ToUpperInvariant())
}

function Required-Text {
    param(
        [object]$Row,
        [string]$Name,
        [int]$RowNumber
    )
    $property = $Row.PSObject.Properties[$Name]
    $value = if ($null -eq $property) { "" } else { [string]$property.Value }
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "CSV $RowNumber 행의 $Name 값이 비어 있습니다."
    }
    return $value.Trim()
}

if (-not (Test-Path -LiteralPath $CsvPath -PathType Leaf)) {
    throw "CSV 파일을 찾을 수 없습니다: $CsvPath"
}
if (-not (Get-Module -ListAvailable -Name PnP.PowerShell)) {
    throw "PnP.PowerShell이 없습니다. PowerShell 7.4 이상에서 Install-Module PnP.PowerShell -Scope CurrentUser를 실행하세요."
}

$rawRows = @(Import-Csv -LiteralPath $CsvPath -Encoding UTF8)
if ($rawRows.Count -eq 0) {
    throw "CSV에 상품 데이터가 없습니다."
}

$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$normalizedRows = [System.Collections.Generic.List[object]]::new()
$versions = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)

for ($index = 0; $index -lt $rawRows.Count; $index++) {
    $row = $rawRows[$index]
    $rowNumber = $index + 2
    $fnsku = Normalize-Fnsku $row.FNSKU
    if ([string]::IsNullOrWhiteSpace($fnsku)) {
        throw "CSV $rowNumber 행의 FNSKU가 비어 있습니다."
    }
    $statusValue = if ($null -eq $row.PSObject.Properties["Status"] -or [string]::IsNullOrWhiteSpace($row.Status)) {
        "Published"
    } else {
        ([string]$row.Status).Trim()
    }
    if ($statusValue -notin @("Published", "Inactive")) {
        throw "CSV $rowNumber 행의 Status는 Published 또는 Inactive여야 합니다."
    }

    $schemaText = Required-Text $row "SchemaVersion" $rowNumber
    $schema = 0
    if (-not [int]::TryParse($schemaText, [ref]$schema) -or $schema -ne 2) {
        throw "CSV $rowNumber 행의 SchemaVersion은 2여야 합니다."
    }

    $rowVersion = if ([string]::IsNullOrWhiteSpace($DataVersion)) {
        Required-Text $row "DataVersion" $rowNumber
    } else {
        $DataVersion.Trim()
    }
    [void]$versions.Add($rowVersion)

    $itemCode = ""
    $sku = ""
    $countryCode = ""
    $countryName = ""
    $productName = ""
    if ($statusValue -eq "Published") {
        $itemCode = Required-Text $row "ItemCode" $rowNumber
        $sku = Required-Text $row "SKU" $rowNumber
        $countryCode = Required-Text $row "CountryCode" $rowNumber
        $countryName = Required-Text $row "CountryName" $rowNumber
        $productName = Required-Text $row "ProductName" $rowNumber
    } else {
        $itemCode = [string]$row.ItemCode
        $sku = [string]$row.SKU
        $countryCode = [string]$row.CountryCode
        $countryName = [string]$row.CountryName
        $productName = [string]$row.ProductName
    }

    $countryCode = Normalize-CountryCode $countryCode
    if ([string]::IsNullOrWhiteSpace($countryCode)) {
        throw "CSV $rowNumber 행의 CountryCode가 비어 있습니다."
    }
    $lookupKey = "$fnsku|$countryCode"
    $providedLookupKey = Required-Text $row "LookupKey" $rowNumber
    if ($providedLookupKey.Trim().ToUpperInvariant() -ne $lookupKey) {
        throw "CSV $rowNumber 행의 LookupKey가 계산값과 다릅니다. 필요: $lookupKey"
    }
    if (-not $seen.Add($lookupKey)) {
        throw "CSV에 중복 FNSKU+국가가 있습니다: $lookupKey"
    }

    $sourceModifiedAt = Required-Text $row "SourceModifiedAt" $rowNumber
    $parsedDate = [datetimeoffset]::MinValue
    if (-not [datetimeoffset]::TryParse($sourceModifiedAt, [ref]$parsedDate)) {
        throw "CSV $rowNumber 행의 SourceModifiedAt은 ISO 날짜/시간이어야 합니다."
    }

    $normalizedRows.Add([pscustomobject]@{
        FNSKU            = $fnsku
        ItemCode         = $itemCode.Trim()
        SKU              = $sku.Trim()
        CountryCode      = $countryCode
        CountryName      = $countryName.Trim()
        ProductName      = $productName.Trim()
        ProductNameEn    = ([string]$row.ProductNameEn).Trim()
        AmazonAccount    = ([string]$row.AmazonAccount).Trim()
        Status           = $statusValue
        SourceModifiedAt = $parsedDate.UtcDateTime.ToString("o")
        DataVersion      = $rowVersion
        SchemaVersion    = 2
        LookupKey        = $lookupKey
    })
}

if ($versions.Count -ne 1) {
    throw "한 번의 업로드에는 동일한 DataVersion만 있어야 합니다. 발견된 버전: $($versions -join ', ')"
}

Write-Host "사전검증 완료: $($normalizedRows.Count)건 / DataVersion $($versions | Select-Object -First 1)"
if (-not $Apply) {
    Write-Host "DRY RUN입니다. SharePoint는 변경하지 않았습니다. 실제 반영은 -Apply를 추가하세요."
    return
}

Import-Module PnP.PowerShell
Connect-PnPOnline -Url $SiteUrl -Interactive -ClientId $ClientId

$existingByLookupKey = @{}
$existingItems = @(Get-PnPListItem -List $ListName -PageSize 2000 -Fields "LookupKey", "Status")
foreach ($item in $existingItems) {
    $key = ([string]$item["LookupKey"]).Trim().ToUpperInvariant()
    if ([string]::IsNullOrWhiteSpace($key)) { continue }
    if ($existingByLookupKey.ContainsKey($key)) {
        throw "SharePoint List에 중복 LookupKey가 있습니다: $key"
    }
    $existingByLookupKey[$key] = $item.Id
}

$created = 0
$updated = 0
$batch = New-PnPBatch
$queued = 0

foreach ($row in $normalizedRows) {
    $values = @{
        "Title"            = $row.LookupKey
        "FNSKU"            = $row.FNSKU
        "ItemCode"         = $row.ItemCode
        "SKU"              = $row.SKU
        "CountryCode"      = $row.CountryCode
        "LookupKey"        = $row.LookupKey
        "CountryName"      = $row.CountryName
        "ProductName"      = $row.ProductName
        "ProductNameEn"    = $row.ProductNameEn
        "AmazonAccount"    = $row.AmazonAccount
        "Status"           = $row.Status
        "SourceModifiedAt" = $row.SourceModifiedAt
        "DataVersion"      = $row.DataVersion
        "SchemaVersion"    = $row.SchemaVersion
    }
    if ($existingByLookupKey.ContainsKey($row.LookupKey)) {
        Set-PnPListItem -List $ListName -Identity $existingByLookupKey[$row.LookupKey] -Values $values -Batch $batch | Out-Null
        $updated++
    } else {
        Add-PnPListItem -List $ListName -Values $values -Batch $batch | Out-Null
        $created++
    }
    $queued++
    if ($queued -ge $BatchSize) {
        Invoke-PnPBatch -Batch $batch -StopOnException
        Write-Progress -Activity "SharePoint 상품 반영" -Status "$($created + $updated) / $($normalizedRows.Count)"
        $batch = New-PnPBatch
        $queued = 0
    }
}
if ($queued -gt 0) {
    Invoke-PnPBatch -Batch $batch -StopOnException
}

$deactivated = 0
if ($DeactivateMissing) {
    $incoming = [System.Collections.Generic.HashSet[string]]::new($seen, [System.StringComparer]::OrdinalIgnoreCase)
    $missingKeys = @($existingByLookupKey.Keys | Where-Object { -not $incoming.Contains($_) })
    $batch = New-PnPBatch
    $queued = 0
    foreach ($key in $missingKeys) {
        Set-PnPListItem -List $ListName -Identity $existingByLookupKey[$key] -Values @{ "Status" = "Inactive" } -Batch $batch | Out-Null
        $deactivated++
        $queued++
        if ($queued -ge $BatchSize) {
            Invoke-PnPBatch -Batch $batch -StopOnException
            $batch = New-PnPBatch
            $queued = 0
        }
    }
    if ($queued -gt 0) {
        Invoke-PnPBatch -Batch $batch -StopOnException
    }
}

Write-Progress -Activity "SharePoint 상품 반영" -Completed
Write-Host "완료: 신규 $created건 / 수정 $updated건 / 미포함 비활성화 $deactivated건"
