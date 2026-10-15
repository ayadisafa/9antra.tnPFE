param([Parameter(Mandatory = $true)][string]$Path)
# Reads the text in a picture with the reader that comes with Windows, and
# prints it as JSON: {"language", "width", "height", "lines": [{"y", "h", "text"}]}.
# Called by app/services/feedback.py; it needs nothing installed.
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } |
    Select-Object -First 1

function Await($operation, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    $task.Wait(-1) | Out-Null
    $task.Result
}

# French first (the feedback is in French or in Tunisian written with Latin
# letters), then English, then whatever language this computer reads.
$engine = $null
$used = ""
foreach ($tag in "fr-FR", "fr-CA", "en-US", "en-GB") {
    $language = New-Object Windows.Globalization.Language($tag)
    if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($language)) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        $used = $tag
        break
    }
}
if ($null -eq $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -ne $engine) { $used = $engine.RecognizerLanguage.LanguageTag }
}
if ($null -eq $engine) { throw "This computer has no language installed for reading text in pictures." }

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path -LiteralPath $Path).Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    foreach ($line in $result.Lines) {
        $top = [int](($line.Words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum)
        $tall = [int](($line.Words | ForEach-Object { $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum)
        $lines += [pscustomobject]@{ y = $top; h = $tall; text = $line.Text }
    }
    [pscustomobject]@{
        language = $used
        width    = [int]$decoder.PixelWidth
        height   = [int]$decoder.PixelHeight
        lines    = @($lines)
    } | ConvertTo-Json -Depth 4 -Compress
}
finally {
    $stream.Dispose()
}
