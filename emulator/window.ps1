param([switch]$SmokeTest, [switch]$ScreenTest, [switch]$KeyTest, [switch]$NetworkTest, [switch]$CardTest,
      [string]$LoadState, [string]$TestDirectory, [string]$Firmware, [switch]$DebuggerTest)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$script:projectRoot = Split-Path -Parent $PSScriptRoot
$script:pythonPath = Join-Path $script:projectRoot '.venv\Scripts\python.exe'
$script:outputDirectory = Join-Path $script:projectRoot 'analysis\emulator'
if ($TestDirectory) { $script:outputDirectory = [System.IO.Path]::GetFullPath($TestDirectory) }
if ($NetworkTest -and (-not $LoadState -or -not $TestDirectory)) { throw 'NetworkTest requires an isolated TestDirectory and LoadState.' }
if ($CardTest -and (-not $LoadState -or -not $TestDirectory)) { throw 'CardTest requires an isolated TestDirectory and LoadState.' }
if ($DebuggerTest -and (-not $Firmware -or -not $TestDirectory -or $LoadState)) { throw 'DebuggerTest requires a fixture Firmware and isolated TestDirectory, without LoadState.' }
$script:reportPath = Join-Path $script:outputDirectory 'window-latest.json'
$script:stdoutPath = Join-Path $script:outputDirectory 'window-stdout.log'
$script:stderrPath = Join-Path $script:outputDirectory 'window-stderr.log'
if ($ScreenTest -or $KeyTest) {
    $script:reportPath = Join-Path $script:outputDirectory 'window-screen-test.json'
    $script:stdoutPath = Join-Path $script:outputDirectory 'window-screen-test-stdout.log'
    $script:stderrPath = Join-Path $script:outputDirectory 'window-screen-test-stderr.log'
}
if ($KeyTest) {
    $script:reportPath = Join-Path $script:outputDirectory 'window-key-test.json'
    $script:stdoutPath = Join-Path $script:outputDirectory 'window-key-test-stdout.log'
    $script:stderrPath = Join-Path $script:outputDirectory 'window-key-test-stderr.log'
}
$script:virtualNorPath = Join-Path $script:outputDirectory 'window-nor-v2.bin'
$script:cardImagePath = Join-Path $script:outputDirectory 'sd-card.img'
$script:cardSettingsPath = Join-Path $script:outputDirectory 'window-card.json'
if (Test-Path -LiteralPath $script:cardSettingsPath) {
    try {
        $savedCard = Get-Content -LiteralPath $script:cardSettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($savedCard.path -is [string] -and (Test-Path -LiteralPath $savedCard.path -PathType Leaf)) {
            $script:cardImagePath = $savedCard.path
        }
    } catch { } # Invalid optional UI preferences do not prevent startup.
}
$script:bootProcess = $null
$script:frame = $null
$script:framePath = $null
$script:report = $null
$script:liveFramePath = Join-Path $script:outputDirectory ("window-live-$PID.png")
$script:liveStamp = [DateTime]::MinValue
$script:bootStarted = [DateTime]::MinValue
$script:smokeRestarts = 0
$script:smokeFailure = $null
$script:phoneButtons = @{}
$script:pressedSources = @{}
$script:pcKeys = @{}
$script:mouseClicks = @{}
$script:sessionReady = $false
$script:sessionPaused = $true
$script:lcdBacklight = $true
$script:commandPath = $null
$script:sessionStatusPath = $null
$script:keyTestStage = 0
$script:keyTestSettled = $false
$script:keyTestAt = [DateTime]::MinValue
$script:keyTestEvents = 0
$script:keyTestPixels = 0
$script:keyTestNavigationRetried = $false
$script:tooltip = New-Object System.Windows.Forms.ToolTip
$script:networkEnabled = $false
$script:networkPlmn = '25001'
$script:networkSim = $true
$script:networkRegistered = $true
$script:networkState = $null
$script:networkReportPath = $null
$script:networkTestStage = 0
$script:networkTestUrlRequested = $false
$script:networkTestBrowserRequested = $false
$script:cardTestDone = $false
$script:baseOutputDirectory = $script:outputDirectory
$script:firmwarePath = $Firmware
$script:firmwareIdentity = $null
$script:debugStartPaused = [bool]$DebuggerTest
$script:breakpoints = @()
$script:debugState = $null
$script:debugDialog = $null
$script:debugTestStarted = $false

$script:form = New-Object System.Windows.Forms.Form
$form.Text = 'Explay B220'
$form.StartPosition = 'CenterScreen'
$form.BackColor = [System.Drawing.Color]::FromArgb(240, 242, 245)
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.AutoScaleMode = 'None'
$form.KeyPreview = $true

$script:title = New-Object System.Windows.Forms.Label
$title.Text = 'Экран Explay B220'
$title.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 17)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(22, 8)
$form.Controls.Add($title)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = 'Оригинальная прошивка · 176 × 220'
$subtitle.ForeColor = [System.Drawing.Color]::FromArgb(85, 96, 112)
$subtitle.AutoSize = $true
$subtitle.Location = New-Object System.Drawing.Point(24, 45)
$form.Controls.Add($subtitle)

$script:screen = New-Object System.Windows.Forms.PictureBox
$screen.BackColor = [System.Drawing.Color]::Black
$screen.Location = New-Object System.Drawing.Point(24, 76)
$screen.Add_Paint({
    param($sender, $paintArgs)
    if ($script:frame) {
        $paintArgs.Graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
        $paintArgs.Graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
        $paintArgs.Graphics.DrawImage($script:frame, $sender.ClientRectangle)
        if (-not $script:lcdBacklight) {
            $shade = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(220,0,0,0))
            try { $paintArgs.Graphics.FillRectangle($shade,$sender.ClientRectangle) }
            finally { $shade.Dispose() }
        }
    }
})
$form.Controls.Add($screen)

$script:phonePanel = New-Object System.Windows.Forms.Panel
$phonePanel.Size = New-Object System.Drawing.Size(352, 222)
$phonePanel.BackColor = [System.Drawing.Color]::FromArgb(26, 29, 35)
$phonePanel.Enabled = $false
$form.Controls.Add($phonePanel)

function Write-B220Command($value) {
    if (-not $script:commandPath -or -not $script:bootProcess -or $script:bootProcess.HasExited) { return }
    $line = ($value | ConvertTo-Json -Compress) + [Environment]::NewLine
    [System.IO.File]::AppendAllText($script:commandPath, $line, (New-Object System.Text.UTF8Encoding($false)))
}

function Send-B220Key([string]$name, [bool]$down, [string]$source) {
    if (-not $script:sessionReady) { return }
    if ($down) {
        if ($script:pressedSources.ContainsKey($source)) { return }
        $alreadyHeld = @($script:pressedSources.Values) -contains $name
        $script:pressedSources[$source] = $name
        if ($alreadyHeld) { return }
    } else {
        if (-not $script:pressedSources.ContainsKey($source)) { return }
        $script:pressedSources.Remove($source)
        if (@($script:pressedSources.Values) -contains $name) { return }
    }
    Write-B220Command @{action='key'; key=$name; down=$down}
}

function Release-B220Keys {
    if ($script:pressedSources.Count) { Write-B220Command @{action='release_all'} }
    $script:pressedSources.Clear()
    $script:pcKeys.Clear()
}

function Add-B220PhoneKey([string]$name, [string]$text, [int]$x, [int]$y, [int]$width, [string]$hint) {
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $text
    $button.Tag = $name
    $button.SetBounds($x,$y,$width,28)
    $button.TabStop = $false
    $button.FlatStyle = 'Flat'
    $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(85, 91, 105)
    $button.FlatAppearance.MouseDownBackColor = [System.Drawing.Color]::FromArgb(66, 91, 116)
    $button.BackColor = [System.Drawing.Color]::FromArgb(38, 43, 53)
    $button.ForeColor = [System.Drawing.Color]::WhiteSmoke
    $button.Font = New-Object System.Drawing.Font('Segoe UI', 9)
    if ($name -like 'call_*') { $button.ForeColor = [System.Drawing.Color]::LightGreen }
    if ($name -eq 'end') { $button.ForeColor = [System.Drawing.Color]::Salmon }
    $button.Add_MouseDown({ param($sender,$eventArgs)
        if ($eventArgs.Button -eq 'Left') {
            $script:mouseClicks[[string]$sender.Tag] = $true
            $sender.Capture = $true
            Send-B220Key ([string]$sender.Tag) $true ('mouse:'+[string]$sender.Tag)
        }
    })
    $button.Add_MouseUp({ param($sender,$eventArgs)
        if ($eventArgs.Button -eq 'Left') { Send-B220Key ([string]$sender.Tag) $false ('mouse:'+[string]$sender.Tag) }
    })
    $button.Add_MouseCaptureChanged({ param($sender,$eventArgs)
        if (-not $sender.Capture) { Send-B220Key ([string]$sender.Tag) $false ('mouse:'+[string]$sender.Tag) }
    })
    $button.Add_Click({ param($sender,$eventArgs)
        $name = [string]$sender.Tag
        if ($script:mouseClicks.ContainsKey($name)) { $script:mouseClicks.Remove($name); return }
        Send-B220Key $name $true ('click:'+$name)
        Send-B220Key $name $false ('click:'+$name)
    })
    $script:tooltip.SetToolTip($button,$hint)
    $script:phonePanel.Controls.Add($button)
    $script:phoneButtons[$name] = $button
}

Add-B220PhoneKey 'soft_left' '—' 4 4 68 'Левая экранная клавиша · F1'
Add-B220PhoneKey 'up' '▲' 145 4 62 'Вверх · ↑'
Add-B220PhoneKey 'soft_right' '—' 280 4 68 'Правая экранная клавиша · F2'
Add-B220PhoneKey 'call_2' '☎ 2' 4 34 68 'Вызов с SIM 2 · F3'
Add-B220PhoneKey 'left' '◀' 77 34 62 'Влево · ←'
Add-B220PhoneKey 'ok' 'OK' 145 34 62 'Центральная клавиша · Enter'
Add-B220PhoneKey 'right' '▶' 213 34 62 'Вправо · →'
Add-B220PhoneKey 'call_3' '☎ 3' 280 34 68 'Вызов с SIM 3 · F4'
Add-B220PhoneKey 'call_1' '☎ 1' 4 64 68 'Вызов с SIM 1 · F5'
Add-B220PhoneKey 'down' '▼' 145 64 62 'Вниз · ↓'
Add-B220PhoneKey 'end' '⏻' 280 64 68 'Завершение / питание · Esc или End'
$digitLabels = @('1','2  АБВГ · ABC','3  ДЕЁЖЗ · DEF','4  ИЙКЛ · GHI','5  МНОП · JKL','6  РСТУ · MNO','7  ФХЦЧ · PQRS','8  ШЩЪЫ · TUV','9  ЬЭЮЯ · WXYZ','*  +','0','#')
$digitNames = @('1','2','3','4','5','6','7','8','9','star','0','hash')
for ($i=0; $i -lt 12; $i++) {
    $name = $digitNames[$i]
    $hint = if ($name -eq 'star') { '* · NumPad * или Shift+8' } elseif ($name -eq 'hash') { '# · NumPad / или Shift+3' } else { $name+' · основная клавиатура или NumPad' }
    Add-B220PhoneKey $name $digitLabels[$i] (4+116*($i%3)) (98+30*[Math]::Floor($i/3)) 112 $hint
    $script:phoneButtons[$name].Font = New-Object System.Drawing.Font('Segoe UI', 8)
}

$script:status = New-Object System.Windows.Forms.Label
$status.AutoSize = $false
$status.Height = 26
$status.Text = 'Подготовка запуска…'
$form.Controls.Add($status)

$script:detail = New-Object System.Windows.Forms.Label
$detail.AutoSize = $false
$detail.Height = 43
$detail.ForeColor = $subtitle.ForeColor
$detail.Text = 'Экран появится после выполнения стартового кода.'
$form.Controls.Add($detail)

$script:progress = New-Object System.Windows.Forms.ProgressBar
$progress.Height = 5
$progress.Style = 'Marquee'
$form.Controls.Add($progress)

$script:restart = New-Object System.Windows.Forms.Button
$restart.Text = 'Перезапустить'
$restart.Size = New-Object System.Drawing.Size(146, 34)
$restart.Enabled = $false
$restart.Add_Click({ Start-B220Boot })
$form.Controls.Add($restart)

$script:pause = New-Object System.Windows.Forms.Button
$pause.Text = 'Продолжить'
$pause.Enabled = $false
$pause.Add_Click({ Write-B220Command @{action=$(if ($script:sessionPaused) {'resume'} else {'pause'})} })
$form.Controls.Add($pause)

$script:save = New-Object System.Windows.Forms.Button
$save.Text = 'Сохранить экран…'
$save.Size = New-Object System.Drawing.Size(164, 34)
$save.Enabled = $false
$save.Add_Click({
    $dialog = New-Object System.Windows.Forms.SaveFileDialog
    try {
        $dialog.Filter = 'PNG (*.png)|*.png'
        $dialog.FileName = 'Explay-B220.png'
        $dialog.Title = 'Сохранить экран телефона'
        if ($dialog.ShowDialog($script:form) -eq 'OK') {
            $script:frame.Save($dialog.FileName, [System.Drawing.Imaging.ImageFormat]::Png)
        }
    } catch {
        [void][System.Windows.Forms.MessageBox]::Show($script:form, $_.Exception.Message, 'Не удалось сохранить экран')
    } finally { $dialog.Dispose() }
})
$form.Controls.Add($save)

$script:journal = New-Object System.Windows.Forms.LinkLabel
$journal.Text = 'Журнал запуска'
$journal.AutoSize = $true
$journal.Enabled = $false
$journal.Add_LinkClicked({
    try { Start-Process -FilePath ([System.IO.Path]::ChangeExtension($script:reportPath, '.html')) }
    catch { [void][System.Windows.Forms.MessageBox]::Show($script:form, $_.Exception.Message, 'Не удалось открыть журнал') }
})
$form.Controls.Add($journal)

$script:zoom = New-Object System.Windows.Forms.ComboBox
$zoom.DropDownStyle = 'DropDownList'
$zoom.Size = New-Object System.Drawing.Size(72, 28)
$maxScale = [Math]::Max(1, [Math]::Min(4, [Math]::Floor(([System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Height - 548) / 220)))
for ($scaleOption = 1; $scaleOption -le $maxScale; $scaleOption++) { [void]$zoom.Items.Add("${scaleOption}×") }
$zoom.Add_SelectedIndexChanged({ Set-B220Layout })
$form.Controls.Add($zoom)

$script:networkButton = New-Object System.Windows.Forms.Button
$networkButton.Text = 'Сеть…'
$networkButton.Add_Click({ Show-B220NetworkSettings })
$form.Controls.Add($networkButton)
$script:cardButton = New-Object System.Windows.Forms.Button
$cardButton.Text = 'Карта…'
$cardButton.Add_Click({ Show-B220CardSettings })
$form.Controls.Add($cardButton)
$script:firmwareButton = New-Object System.Windows.Forms.Button
$firmwareButton.Text = 'Прошивка…'
$firmwareButton.Add_Click({ Show-B220FirmwareSettings })
$form.Controls.Add($firmwareButton)
$script:debugButton = New-Object System.Windows.Forms.Button
$debugButton.Text = 'Отладка…'
$debugButton.Add_Click({ Show-B220Debugger })
$form.Controls.Add($debugButton)
$script:networkLabel = New-Object System.Windows.Forms.Label
$networkLabel.Font = New-Object System.Drawing.Font('Segoe UI', 8)
$networkLabel.Text = 'Интернет выключен'
$form.Controls.Add($networkLabel)
$script:networkJournal = New-Object System.Windows.Forms.LinkLabel
$networkJournal.Text = 'Журнал сети'
$networkJournal.AutoSize = $true
$networkJournal.Enabled = $false
$networkJournal.Add_LinkClicked({
    if ($script:networkReportPath -and (Test-Path -LiteralPath $script:networkReportPath)) {
        Start-Process -FilePath notepad.exe -ArgumentList @('"'+$script:networkReportPath+'"')
    }
})
$form.Controls.Add($networkJournal)

function Ensure-B220Card {
    if (-not (Test-Path -LiteralPath $script:cardImagePath)) {
        $result = & $script:pythonPath -X utf8 -m tools.sd_image $script:cardImagePath --create --sample 2>&1
        if ($LASTEXITCODE -ne 0) { throw ($result -join [Environment]::NewLine) }
    }
}

function Set-B220FirmwarePaths {
    $script:outputDirectory = $script:baseOutputDirectory
    if ($script:firmwarePath) {
        $result = & $script:pythonPath -X utf8 -m emulator.firmware $script:firmwarePath 2>&1
        if ($LASTEXITCODE -ne 0) { throw ($result -join [Environment]::NewLine) }
        $script:firmwareIdentity = ($result -join '') | ConvertFrom-Json
        $script:outputDirectory = Join-Path $script:baseOutputDirectory ('experiments\'+$script:firmwareIdentity.sha256)
        $script:subtitle.Text = 'Эксперимент · '+$script:firmwareIdentity.sha256.Substring(0,12)
        # A modified PS keeps the network when its network code equals the original.
        $script:networkButton.Enabled = [bool]$script:firmwareIdentity.network
        if (-not $script:networkButton.Enabled) { $script:networkEnabled = $false }
    } else {
        $script:firmwareIdentity = $null
        $script:subtitle.Text = 'Оригинальная прошивка · 176 × 220'
        $script:networkButton.Enabled = $true
    }
    $script:virtualNorPath = Join-Path $script:outputDirectory 'window-nor-v2.bin'
    $script:cardImagePath = Join-Path $script:outputDirectory 'sd-card.img'
    $script:cardSettingsPath = Join-Path $script:outputDirectory 'window-card.json'
    if (-not $script:firmwarePath -and (Test-Path -LiteralPath $script:cardSettingsPath)) {
        try {
            $settings = Get-Content -LiteralPath $script:cardSettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($settings.path -is [string] -and (Test-Path -LiteralPath $settings.path -PathType Leaf)) { $script:cardImagePath = $settings.path }
        } catch { }
    }
    if (-not $SmokeTest -and -not $ScreenTest -and -not $KeyTest) {
        $script:reportPath = Join-Path $script:outputDirectory 'window-latest.json'
        $script:stdoutPath = Join-Path $script:outputDirectory 'window-stdout.log'
        $script:stderrPath = Join-Path $script:outputDirectory 'window-stderr.log'
    }
    $script:liveFramePath = Join-Path $script:outputDirectory ("window-live-$PID.png")
}

function Show-B220FirmwareSettings {
    Release-B220Keys
    $dialog = New-Object System.Windows.Forms.Form
    $dialog.Text = 'Образ прошивки'
    $dialog.Font = $script:form.Font
    $dialog.ClientSize = New-Object System.Drawing.Size(540,320)
    $dialog.StartPosition = 'CenterParent'
    $dialog.FormBorderStyle = 'FixedDialog'
    $dialog.MaximizeBox = $false
    $dialog.MinimizeBox = $false
    $info = New-Object System.Windows.Forms.TextBox
    $info.Multiline = $true
    $info.ReadOnly = $true
    $info.ScrollBars = 'Vertical'
    $info.SetBounds(16,16,508,142)
    $info.Text = if ($script:firmwareIdentity) {
        "Экспериментальный PS: $($script:firmwareIdentity.source)`r`nSHA-256: $($script:firmwareIdentity.sha256)`r`nРазмер: $($script:firmwareIdentity.size) Б`r`nДанные: $script:outputDirectory"
    } else { 'Выбран оригинальный PS из analysis\extracted.' }
    $dialog.Controls.Add($info)
    $hint = New-Object System.Windows.Forms.Label
    $hint.Text = 'Выберите отдельную копию raw PS того же размера. Для каждого хеша создаются свои NOR и SD. Выбор перезапускает телефон; исходный файл не записывается. Интернет HLE доступен в режиме оригинала.'
    $hint.SetBounds(16,166,508,62)
    $dialog.Controls.Add($hint)
    $pauseBox = New-Object System.Windows.Forms.CheckBox
    $pauseBox.Text = 'Остановиться перед первой инструкцией'
    $pauseBox.Checked = $true
    $pauseBox.SetBounds(16,230,508,26)
    $dialog.Controls.Add($pauseBox)
    $choose = New-Object System.Windows.Forms.Button
    $choose.Text = 'Выбрать PS…'
    $choose.SetBounds(16,272,160,32)
    $choose.Add_Click({
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        $picker.Filter = 'Raw PS (*.bin)|*.bin|Все файлы (*.*)|*.*'
        try {
            if ($picker.ShowDialog($dialog) -ne 'OK') { return }
            $check = & $script:pythonPath -X utf8 -m emulator.firmware $picker.FileName 2>&1
            if ($LASTEXITCODE -ne 0) { throw ($check -join [Environment]::NewLine) }
            $script:firmwarePath = $picker.FileName
            $script:LoadState = $null
            $script:debugStartPaused = $pauseBox.Checked
            $dialog.Close()
            Start-B220Boot
        } catch { [void][System.Windows.Forms.MessageBox]::Show($dialog,$_.Exception.Message,'Образ прошивки') }
        finally { $picker.Dispose() }
    })
    $dialog.Controls.Add($choose)
    $original = New-Object System.Windows.Forms.Button
    $original.Text = 'Оригинал'
    $original.SetBounds(188,272,160,32)
    $original.Add_Click({
        $script:firmwarePath = $null
        $script:LoadState = $null
        $script:debugStartPaused = $pauseBox.Checked
        $dialog.Close()
        Start-B220Boot
    })
    $dialog.Controls.Add($original)
    $close = New-Object System.Windows.Forms.Button
    $close.Text = 'Закрыть'
    $close.SetBounds(360,272,164,32)
    $close.Add_Click({ $dialog.Close() })
    $dialog.Controls.Add($close)
    try { [void]$dialog.ShowDialog($script:form) } finally { $dialog.Dispose() }
}

function Show-B220Debugger {
    Release-B220Keys
    $script:debugDialog = New-Object System.Windows.Forms.Form
    $d = $script:debugDialog
    $d.Text = 'Отладчик ARM / Thumb'
    $d.Font = $script:form.Font
    $d.ClientSize = New-Object System.Drawing.Size(680,650)
    $d.StartPosition = 'CenterParent'
    $d.FormBorderStyle = 'FixedDialog'
    $d.MaximizeBox = $false
    $d.MinimizeBox = $false
    $stop = New-Object System.Windows.Forms.Button
    $stop.Text = 'Пауза'
    $stop.SetBounds(16,12,130,30)
    $stop.Add_Click({ Write-B220Command @{action='pause'} })
    $d.Controls.Add($stop)
    $continue = New-Object System.Windows.Forms.Button
    $continue.Text = 'Продолжить'
    $continue.SetBounds(156,12,130,30)
    $continue.Add_Click({ Write-B220Command @{action='resume'} })
    $d.Controls.Add($continue)
    $step = New-Object System.Windows.Forms.Button
    $step.Text = '1 инструкция'
    $step.SetBounds(296,12,140,30)
    $step.Add_Click({ Write-B220Command @{action='debug';operation='step'} })
    $d.Controls.Add($step)
    $caption = New-Object System.Windows.Forms.Label
    $caption.Text = 'Точки останова: адреса через пробел, например 0x483978 0x394320'
    $caption.SetBounds(16,54,648,24)
    $d.Controls.Add($caption)
    $points = New-Object System.Windows.Forms.TextBox
    $points.Text = $script:breakpoints -join ' '
    $points.SetBounds(16,80,488,28)
    $d.Controls.Add($points)
    $apply = New-Object System.Windows.Forms.Button
    $apply.Text = 'Применить'
    $apply.SetBounds(514,78,150,30)
    $apply.Add_Click({
        try {
            $values = @($points.Text.Trim() -split '[,;\s]+' | Where-Object { $_ })
            if ($values.Count -gt 64) { throw 'Допустимо до 64 адресов.' }
            foreach ($value in $values) {
                if ($value -match '^0[xX][0-9a-fA-F]+$') { [void][Convert]::ToUInt32($value.Substring(2),16) }
                elseif ($value -match '^\d+$') { [void][Convert]::ToUInt32($value,10) }
                else { throw ('Неверный адрес: '+$value) }
            }
            $script:breakpoints = $values
            Write-B220Command @{action='debug';operation='breakpoints';addresses=@($values)}
        } catch { [void][System.Windows.Forms.MessageBox]::Show($d,$_.Exception.Message,'Точки останова') }
    })
    $d.Controls.Add($apply)
    $memory = New-Object System.Windows.Forms.TextBox
    $memory.Text = '0x04000000'
    $memory.SetBounds(16,120,220,28)
    $d.Controls.Add($memory)
    $read = New-Object System.Windows.Forms.Button
    $read.Text = 'Прочитать 128 Б'
    $read.SetBounds(246,118,190,30)
    $read.Add_Click({ Write-B220Command @{action='debug';operation='inspect';address=$memory.Text} })
    $d.Controls.Add($read)
    $script:debugText = New-Object System.Windows.Forms.TextBox
    $script:debugText.Multiline = $true
    $script:debugText.ReadOnly = $true
    $script:debugText.ScrollBars = 'Both'
    $script:debugText.WordWrap = $false
    $script:debugText.Font = New-Object System.Drawing.Font('Consolas',10)
    $script:debugText.SetBounds(16,162,648,434)
    $d.Controls.Add($script:debugText)
    $hint = New-Object System.Windows.Forms.Label
    $hint.Text = 'Регистры и память читаются на паузе. Закрытие окна сохраняет паузу.'
    $hint.SetBounds(16,608,648,28)
    $d.Controls.Add($hint)
    Write-B220Command @{action='pause'}
    $testTimer = $null
    if ($DebuggerTest) {
        $script:debugTestStage = 0
        $script:debugTestAt = [DateTime]::UtcNow
        $testTimer = New-Object System.Windows.Forms.Timer
        $testTimer.Interval = 150
        $testTimer.Add_Tick({
            try {
                if (([DateTime]::UtcNow-$script:debugTestAt).TotalSeconds -gt 30) { throw 'Debugger UI test timeout.' }
                $s = Get-Content -LiteralPath $script:sessionStatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
                Update-B220Debugger $s
                $v = $s.debugger.view
                switch ($script:debugTestStage) {
                    0 {
                        if ($s.state -ne 'paused' -or $v.registers.pc -ne '0x0') { return }
                        $step.PerformClick()
                        $script:debugTestStage = 1
                    }
                    1 {
                        if ($v.registers.pc -ne '0x4') { return }
                        if ($v.registers.r0 -ne '0x1') { throw 'Step did not execute fixture MOV.' }
                        $points.Text = '0x8'
                        $apply.PerformClick()
                        $continue.PerformClick()
                        $script:debugTestStage = 2
                    }
                    2 {
                        if ($s.debugger.hit -ne '0x8') { return }
                        if ($v.registers.r0 -ne '0x2') { throw 'Continue did not reach the breakpoint before branch.' }
                        $memory.Text = '0x0'
                        $read.PerformClick()
                        $script:debugTestStage = 3
                    }
                    3 {
                        if (-not $v.memory) { return }
                        if ($v.memory[0] -notlike '*e3 a0 00 01 e2 80 00 01*') { throw 'Memory inspector returned wrong fixture bytes.' }
                        $d.Refresh()
                        $capture = New-Object System.Drawing.Bitmap($d.Width,$d.Height)
                        try {
                            $d.DrawToBitmap($capture,(New-Object System.Drawing.Rectangle(0,0,$capture.Width,$capture.Height)))
                            $capture.Save((Join-Path $script:outputDirectory 'window-debugger.png'),[System.Drawing.Imaging.ImageFormat]::Png)
                        } finally { $capture.Dispose() }
                        # Continue must bypass only the stopped branch once,
                        # then stop at it again instead of hanging or exiting.
                        $continue.PerformClick()
                        $script:debugTestStage = 4
                    }
                    4 {
                        if ($s.debugger.hit -ne '0x8' -or -not $v -or $v.memory) { return }
                        $testTimer.Stop()
                        [System.IO.File]::WriteAllText((Join-Path $script:outputDirectory 'window-debugger-check.json'),'{"status":"PASS","reset_pause":true,"single_step":true,"breakpoint":true,"resume_rehits":true,"memory":true}')
                        $d.Close()
                    }
                }
            } catch {
                $testTimer.Stop()
                $script:smokeFailure = $_.Exception.Message
                $d.Close()
            }
        })
        $testTimer.Start()
    }
    try { [void]$d.ShowDialog($script:form) }
    finally {
        if ($testTimer) { $testTimer.Dispose() }
        $d.Dispose(); $script:debugDialog = $null; $script:debugText = $null
    }
}

function Update-B220Debugger($state) {
    $script:debugState = $state
    if (-not $script:debugDialog -or -not $state.debugger) { return }
    $view = $state.debugger.view
    $lines = @(('Состояние: '+$state.state), ('Точки: '+($state.debugger.breakpoints -join ', ')))
    if ($state.cpu_stop) { $lines += 'Остановка: '+$state.cpu_stop.reason+' · '+$state.cpu_stop.pc }
    if ($state.input_error) { $lines += 'Ошибка: '+$state.input_error }
    if ($state.state -ne 'paused') { $lines += 'Ниже — последнее чтение на паузе.' }
    if ($view) {
        $lines += 'Режим: '+$view.mode
        $properties = @($view.registers.PSObject.Properties)
        for ($i=0; $i -lt $properties.Count; $i+=4) {
            $row = @()
            for ($j=$i; $j -lt [Math]::Min($i+4,$properties.Count); $j++) {
                $row += ($properties[$j].Name.PadRight(4)+' '+([string]$properties[$j].Value).PadRight(10))
            }
            $lines += $row -join '  '
        }
        $lines += @('', 'Инструкции:') + @($view.disassembly)
        if ($view.memory) { $lines += @('', 'Память:') + @($view.memory) }
    }
    $next = $lines -join "`r`n"
    if ($script:debugText.Text -ne $next) { $script:debugText.Text = $next }
}

function Show-B220CardSettings {
    Release-B220Keys
    $dialog = New-Object System.Windows.Forms.Form
    $dialog.Text = 'Карта памяти'
    $dialog.Font = $script:form.Font
    $dialog.ClientSize = New-Object System.Drawing.Size(440,290)
    $dialog.StartPosition = 'CenterParent'
    $dialog.FormBorderStyle = 'FixedDialog'
    $dialog.MaximizeBox = $false
    $dialog.MinimizeBox = $false
    $description = New-Object System.Windows.Forms.Label
    $description.Text = 'Образ microSD · FAT16 · 16 или 32 МиБ'
    $description.SetBounds(16,16,408,28)
    $dialog.Controls.Add($description)
    $pathBox = New-Object System.Windows.Forms.TextBox
    $pathBox.Text = $script:cardImagePath
    $pathBox.ReadOnly = $true
    $pathBox.SetBounds(16,50,408,28)
    $dialog.Controls.Add($pathBox)
    $help = New-Object System.Windows.Forms.Label
    $help.Text = 'Добавление файлов и смена образа перезапускают телефон. Файлы появятся в корне карты. Длинные имена получают короткие имена вида FILE0001.JPG.'
    $help.SetBounds(16,92,408,70)
    $dialog.Controls.Add($help)
    $add = New-Object System.Windows.Forms.Button
    $add.Text = 'Добавить файлы и перезапустить…'
    $add.SetBounds(16,176,408,32)
    $add.Add_Click({
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        $picker.Title = 'Добавить файлы на карту памяти'
        $picker.Filter = 'Все файлы (*.*)|*.*|Изображения|*.jpg;*.jpeg;*.bmp;*.gif;*.png'
        $picker.Multiselect = $true
        try {
            if ($picker.ShowDialog($dialog) -ne 'OK') { return }
            Stop-B220Child
            Ensure-B220Card
            $result = & $script:pythonPath -X utf8 -m tools.sd_image $script:cardImagePath --add @($picker.FileNames) 2>&1
            if ($LASTEXITCODE -ne 0) { throw ($result -join [Environment]::NewLine) }
            [void][System.Windows.Forms.MessageBox]::Show($dialog,('Добавлены: '+($result -join ', ')),'Карта памяти')
            $dialog.Close()
            Start-B220Boot
        } catch {
            [void][System.Windows.Forms.MessageBox]::Show($dialog,$_.Exception.Message,'Не удалось добавить файлы')
            if (-not $script:bootProcess) { Start-B220Boot }
        } finally { $picker.Dispose() }
    })
    $dialog.Controls.Add($add)
    $select = New-Object System.Windows.Forms.Button
    $select.Text = 'Выбрать другой образ…'
    $select.SetBounds(16,226,250,32)
    $select.Add_Click({
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        $picker.Title = 'Выбрать образ карты (телефон перезапустится)'
        $picker.Filter = 'Образы карты (*.img)|*.img|Все файлы (*.*)|*.*'
        try {
            if ($picker.ShowDialog($dialog) -eq 'OK') {
                $length = (Get-Item -LiteralPath $picker.FileName).Length
                if ($length -notin @(16777216,33554432)) { throw 'Поддерживаются образы размером 16 и 32 МиБ.' }
                Stop-B220Child
                if ($script:firmwarePath) {
                    # Import a copy; an experiment never mounts the ordinary
                    # card image or another image selected on the host directly.
                    $destination = Join-Path $script:outputDirectory 'sd-card.img'
                    if ([System.IO.Path]::GetFullPath($picker.FileName) -ne [System.IO.Path]::GetFullPath($destination)) {
                        Copy-Item -LiteralPath $picker.FileName -Destination $destination -Force
                    }
                    $script:cardImagePath = $destination
                } else { $script:cardImagePath = $picker.FileName }
                [System.IO.File]::WriteAllText($script:cardSettingsPath,(@{path=$script:cardImagePath} | ConvertTo-Json),(New-Object System.Text.UTF8Encoding($false)))
                $dialog.Close()
                Start-B220Boot
            }
        } catch { [void][System.Windows.Forms.MessageBox]::Show($dialog,$_.Exception.Message,'Карта памяти') }
        finally { $picker.Dispose() }
    })
    $dialog.Controls.Add($select)
    $close = New-Object System.Windows.Forms.Button
    $close.Text = 'Закрыть'
    $close.SetBounds(300,226,124,32)
    $close.Add_Click({ $dialog.Close() })
    $dialog.Controls.Add($close)
    if ($CardTest) {
        $dialog.Add_Shown({
            $dialog.Refresh()
            $capture = New-Object System.Drawing.Bitmap($dialog.Width,$dialog.Height)
            try {
                $dialog.DrawToBitmap($capture,(New-Object System.Drawing.Rectangle(0,0,$capture.Width,$capture.Height)))
                $capture.Save((Join-Path $script:outputDirectory 'window-card-settings.png'),[System.Drawing.Imaging.ImageFormat]::Png)
            } finally { $capture.Dispose() }
            $dialog.Close()
        })
    }
    try { [void]$dialog.ShowDialog($script:form) } finally { $dialog.Dispose() }
}

function Show-B220UrlInput {
    $dialog = New-Object System.Windows.Forms.Form
    $dialog.Text = 'Открыть адрес в телефоне'
    $dialog.Font = $script:form.Font
    $dialog.ClientSize = New-Object System.Drawing.Size(420,160)
    $dialog.StartPosition = 'CenterParent'
    $dialog.FormBorderStyle = 'FixedDialog'
    $dialog.MaximizeBox = $false
    $dialog.MinimizeBox = $false
    $label = New-Object System.Windows.Forms.Label
    $label.Text = 'Сначала откройте «Ввести адрес» в браузере телефона.'
    $label.SetBounds(16,14,388,38)
    $dialog.Controls.Add($label)
    $inputBox = New-Object System.Windows.Forms.TextBox
    $inputBox.Text = 'http://'
    $inputBox.MaxLength = 512
    $inputBox.SetBounds(16,56,388,28)
    $dialog.Controls.Add($inputBox)
    $open = New-Object System.Windows.Forms.Button
    $open.Text = 'Открыть'
    $open.SetBounds(284,108,120,30)
    $open.Add_Click({
        Write-B220Command @{action='open_url';url=$inputBox.Text}
        $dialog.Close()
    })
    $dialog.Controls.Add($open)
    $dialog.AcceptButton = $open
    if ($NetworkTest) {
        $dialog.Add_Shown({
            $inputBox.Text = 'http://a.aa/'
            $dialog.Refresh()
            $capture = New-Object System.Drawing.Bitmap($dialog.Width,$dialog.Height)
            try {
                $dialog.DrawToBitmap($capture,(New-Object System.Drawing.Rectangle(0,0,$capture.Width,$capture.Height)))
                $capture.Save((Join-Path $script:outputDirectory 'window-network-url.png'),[System.Drawing.Imaging.ImageFormat]::Png)
            } finally { $capture.Dispose() }
            $open.PerformClick()
        })
    }
    try { [void]$dialog.ShowDialog($script:form) } finally { $dialog.Dispose() }
}

function Show-B220NetworkSettings {
    Release-B220Keys
    $dialog = New-Object System.Windows.Forms.Form
    $dialog.Text = 'Сеть и виртуальная SIM1'
    $dialog.Font = $script:form.Font
    $dialog.ClientSize = New-Object System.Drawing.Size(420,322)
    $dialog.StartPosition = 'CenterParent'
    $dialog.FormBorderStyle = 'FixedDialog'
    $dialog.MaximizeBox = $false
    $dialog.MinimizeBox = $false
    $enabledBox = New-Object System.Windows.Forms.CheckBox
    $enabledBox.Text = 'Интернет через компьютер (эксперимент)'
    $enabledBox.SetBounds(16,16,390,28)
    $enabledBox.Checked = $script:networkEnabled
    $dialog.Controls.Add($enabledBox)
    $help = New-Object System.Windows.Forms.Label
    $help.Text = 'Откройте меню «Интернет» в телефоне. Возможности браузера и ограничения сайтов сохраняются.'
    $help.SetBounds(16,52,388,44)
    $dialog.Controls.Add($help)
    $plmnLabel = New-Object System.Windows.Forms.Label
    $plmnLabel.Text = 'Домашний PLMN (MCC + MNC):'
    $plmnLabel.SetBounds(16,108,255,24)
    $dialog.Controls.Add($plmnLabel)
    $plmnBox = New-Object System.Windows.Forms.TextBox
    $plmnBox.Text = $script:networkPlmn
    $plmnBox.MaxLength = 6
    $plmnBox.Enabled = -not ($script:networkState -and $script:networkState.attached)
    $plmnBox.SetBounds(282,104,122,28)
    $dialog.Controls.Add($plmnBox)
    $simBox = New-Object System.Windows.Forms.CheckBox
    $simBox.Text = 'Виртуальная SIM1 вставлена'
    $simBox.Checked = $script:networkSim
    $simBox.SetBounds(16,146,388,28)
    $dialog.Controls.Add($simBox)
    $registeredBox = New-Object System.Windows.Forms.CheckBox
    $registeredBox.Text = 'Зарегистрирована в сети'
    $registeredBox.Checked = $script:networkRegistered
    $registeredBox.Enabled = $simBox.Checked
    $registeredBox.SetBounds(16,180,388,28)
    $dialog.Controls.Add($registeredBox)
    $simBox.Add_CheckedChanged({
        $registeredBox.Enabled = $simBox.Checked
        if (-not $simBox.Checked) { $registeredBox.Checked = $false }
    })
    $urlButton = New-Object System.Windows.Forms.Button
    $urlButton.Text = 'Открыть URL…'
    $urlButton.SetBounds(16,232,150,30)
    $urlButton.Enabled = $script:sessionReady -and $script:networkState -and $script:networkState.enabled
    $urlButton.Add_Click({ $dialog.Close(); Show-B220UrlInput })
    $dialog.Controls.Add($urlButton)
    $apply = New-Object System.Windows.Forms.Button
    $apply.Text = 'Применить'
    $apply.SetBounds(284,276,120,30)
    $apply.Add_Click({
        if ($plmnBox.Text -notmatch '^[0-9]{5,6}$') {
            [void][System.Windows.Forms.MessageBox]::Show($dialog,'PLMN должен содержать 5 или 6 цифр.','Параметры SIM')
            return
        }
        $script:networkEnabled = $enabledBox.Checked
        $script:networkPlmn = $plmnBox.Text
        $script:networkSim = $simBox.Checked
        $script:networkRegistered = $registeredBox.Checked
        Write-B220Command @{action='network';enabled=$script:networkEnabled;plmn=$script:networkPlmn;sim_present=$script:networkSim;registered=$script:networkRegistered}
        $dialog.Close()
    })
    $dialog.Controls.Add($apply)
    $dialog.AcceptButton = $apply
    if ($NetworkTest) {
        $dialog.Add_Shown({
            $enabledBox.Checked = $script:networkTestStage -ne 4
            if ($script:networkTestStage -eq 3) { $registeredBox.Checked = $false }
            if ($script:networkTestStage -eq 1) {
                $dialog.Refresh()
                $capture = New-Object System.Drawing.Bitmap($dialog.Width,$dialog.Height)
                try {
                    $dialog.DrawToBitmap($capture,(New-Object System.Drawing.Rectangle(0,0,$capture.Width,$capture.Height)))
                    $capture.Save((Join-Path $script:outputDirectory 'window-network-settings.png'),[System.Drawing.Imaging.ImageFormat]::Png)
                } finally { $capture.Dispose() }
            }
            $apply.PerformClick()
        })
    }
    try { [void]$dialog.ShowDialog($script:form) } finally { $dialog.Dispose() }
}

function Set-B220Layout {
    $script:form.AutoScrollPosition = New-Object System.Drawing.Point(0,0)
    $scale = $script:zoom.SelectedIndex + 1
    $width = [Math]::Max(400, 176 * $scale + 48)
    $height = 220 * $scale + 545
    $area = [System.Windows.Forms.Screen]::FromControl($script:form).WorkingArea
    $clientHeight = [Math]::Min($height,($area.Height-40))
    $needsScroll = $clientHeight -lt $height
    $script:form.AutoScrollMinSize = if ($needsScroll) { New-Object System.Drawing.Size($width,$height) } else { [System.Drawing.Size]::Empty }
    $script:form.AutoScroll = $needsScroll
    $clientWidth = $width + $(if ($needsScroll) { [System.Windows.Forms.SystemInformation]::VerticalScrollBarWidth } else { 0 })
    $script:form.ClientSize = New-Object System.Drawing.Size($clientWidth, $clientHeight)
    $script:screen.Size = New-Object System.Drawing.Size((176 * $scale), (220 * $scale))
    $script:screen.Left = [int](($width - $script:screen.Width) / 2)
    $script:phonePanel.Location = New-Object System.Drawing.Point(([int](($width-352)/2)),($script:screen.Bottom+8))
    $bottom = $script:phonePanel.Bottom + 8
    $script:status.SetBounds(24, $bottom, ($width - 48), 26)
    $script:detail.SetBounds(24, ($bottom + 26), ($width - 48), 35)
    $script:progress.SetBounds(24, ($bottom + 62), ($width - 48), 4)
    $buttonWidth = [int](($width-64)/3)
    $script:restart.Text = 'Перезапуск'
    $script:save.Text = 'Сохранить PNG'
    $script:restart.SetBounds(24,($bottom+72),$buttonWidth,30)
    $script:pause.SetBounds((32+$buttonWidth),($bottom+72),$buttonWidth,30)
    $script:save.SetBounds((40+2*$buttonWidth),($bottom+72),$buttonWidth,30)
    $script:journal.Location = New-Object System.Drawing.Point(24, ($bottom + 113))
    $script:zoom.Location = New-Object System.Drawing.Point(($width - 96), ($bottom + 108))
    $script:networkJournal.Location = New-Object System.Drawing.Point(150, ($bottom + 113))
    $script:networkButton.SetBounds(24,($bottom+145),90,30)
    $script:cardButton.SetBounds(122,($bottom+145),90,30)
    $script:networkLabel.SetBounds(222,($bottom+143),($width-246),42)
    $script:firmwareButton.SetBounds(24,($bottom+185),166,30)
    $script:debugButton.SetBounds(198,($bottom+185),154,30)
    $area = [System.Windows.Forms.Screen]::FromControl($script:form).WorkingArea
    if ($script:form.Bottom -gt $area.Bottom) { $script:form.Top = [Math]::Max($area.Top, $area.Bottom - $script:form.Height) }
}

function Show-B220Error([string]$message) {
    $script:progress.Visible = $false
    $script:restart.Enabled = $true
    $script:status.Text = 'Не удалось завершить запуск'
    $script:detail.Text = 'Можно повторить запуск. Подробности — в сообщении об ошибке.'
    if ($SmokeTest -or $ScreenTest -or $KeyTest -or $NetworkTest -or $CardTest -or $DebuggerTest) {
        $script:smokeFailure = $message
        [System.IO.File]::WriteAllText((Join-Path $script:outputDirectory 'window-smoke-error.log'), $message)
        $script:form.Close()
        return
    }
    [void][System.Windows.Forms.MessageBox]::Show($script:form, $message, 'Explay B220 — ошибка', 'OK', 'Error')
}

function Set-B220Frame([string]$path) {
    # Close the file before GDI+ sees the image: retain only an in-memory copy.
    $buffer = New-Object System.IO.MemoryStream
    try {
        $stream = [System.IO.File]::Open($path, 'Open', 'Read', ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
        try { $stream.CopyTo($buffer) }
        finally { $stream.Dispose() }
        $buffer.Position = 0
        $loaded = [System.Drawing.Image]::FromStream($buffer)
        try { $nextFrame = New-Object System.Drawing.Bitmap($loaded) }
        finally { $loaded.Dispose() }
    } finally { $buffer.Dispose() }
    if ($script:frame) { $script:frame.Dispose() }
    $script:frame = $nextFrame
    $script:framePath = $path
    $script:save.Enabled = $true
    $script:screen.Invalidate()
}

function Start-B220Boot {
    try {
        Stop-B220Child
        Set-B220FirmwarePaths
        if (-not (Test-Path -LiteralPath $script:pythonPath)) { throw 'Не найден Python проекта в .venv. Подготовьте окружение по README.md.' }
        $script:restart.Enabled = $false
        $script:save.Enabled = $false
        $script:journal.Enabled = $false
        $script:report = $null
        $script:debugState = $null
        $script:networkState = $null
        $script:networkReportPath = $null
        $script:networkLabel.Text = if ($script:networkEnabled) { 'Ожидается меню «Интернет»' } else { 'Интернет выключен' }
        $script:networkJournal.Enabled = $false
        $script:sessionReady = $false
        $script:lcdBacklight = $true
        $script:phonePanel.Enabled = $false
        $script:pause.Enabled = $false
        $script:framePath = $null
        $script:bootStarted = [DateTime]::UtcNow
        $script:liveStamp = [DateTime]::MinValue
        if ($script:frame) { $script:frame.Dispose(); $script:frame = $null }
        $script:screen.Invalidate()
        $script:status.Text = 'Выполняется загрузка прошивки…'
        $script:detail.Text = 'Экран обновляется при выводе прошивки. Пробный запуск ограничен тремя минутами.'
        $script:progress.Visible = $true
        [void][System.IO.Directory]::CreateDirectory($script:outputDirectory)
        $argsForBoot = @('-X', 'utf8', '-m', 'emulator.run', '--explore', '--instructions', '200000000', '--seconds', '180', '--frames', '5', '--live-frame', ('"' + $script:liveFramePath + '"'), '--out', ('"' + $script:reportPath + '"'))
        if ($script:firmwarePath) {
            $argsForBoot += @('--firmware',('"'+$script:firmwarePath+'"'),'--firmware-sha256',$script:firmwareIdentity.sha256)
        }
        if ($script:debugStartPaused) { $argsForBoot += '--debug-start-paused' }
        foreach ($point in $script:breakpoints) { $argsForBoot += @('--breakpoint',$point) }
        $script:commandPath = $null
        $script:sessionStatusPath = $null
        if (-not $SmokeTest -and -not $ScreenTest) {
            $sessionName = 'window-session-'+$PID+'-'+[Guid]::NewGuid().ToString('N')
            $script:commandPath = Join-Path $script:outputDirectory ($sessionName+'.jsonl')
            $script:sessionStatusPath = Join-Path $script:outputDirectory ($sessionName+'.json')
            [System.IO.File]::WriteAllText($script:commandPath,'',(New-Object System.Text.UTF8Encoding($false)))
            $argsForBoot += @('--interactive','--commands',('"'+$script:commandPath+'"'),'--session-status',('"'+$script:sessionStatusPath+'"'))
        }
        if ($SmokeTest) { $argsForBoot += @('--until', '0x1a0fc2') }
        elseif ($LoadState) { $argsForBoot += @('--load-state',('"'+$LoadState+'"')) }
        else {
            if (-not $ScreenTest -and -not $KeyTest) { $argsForBoot += @('--save-flash', ('"' + $script:virtualNorPath + '"')) }
            if (Test-Path -LiteralPath $script:virtualNorPath) { $argsForBoot += @('--load-flash', ('"' + $script:virtualNorPath + '"')) }
        }
        if (-not $LoadState -and -not $SmokeTest -and -not $ScreenTest -and -not $KeyTest -and -not $NetworkTest) {
            Ensure-B220Card
            $argsForBoot += @('--sd-image',('"'+$script:cardImagePath+'"'))
        }
        if ($NetworkTest) { $argsForBoot += '--window-http-test' }
        $script:bootProcess = Start-Process -FilePath $script:pythonPath -ArgumentList $argsForBoot -WorkingDirectory $script:projectRoot -WindowStyle Hidden -RedirectStandardOutput $script:stdoutPath -RedirectStandardError $script:stderrPath -PassThru
        # Keep the process handle open so ExitCode remains available after exit.
        [void]$script:bootProcess.Handle
        if ($script:networkEnabled) {
            Write-B220Command @{action='network';enabled=$true;plmn=$script:networkPlmn;sim_present=$script:networkSim;registered=$script:networkRegistered}
        }
        $script:timer.Start()
    } catch { Show-B220Error $_.Exception.Message }
}

function Stop-B220Child {
    if ($script:timer) { $script:timer.Stop() }
    if (-not $script:bootProcess) { return }
    try {
        if (-not $script:bootProcess.HasExited) {
            Release-B220Keys
            Write-B220Command @{action='stop'}
            if (-not $script:bootProcess.WaitForExit(2000)) {
                # Windows venv python.exe can be a redirector with a child.
                # Terminate this owned tree if graceful shutdown is stuck.
                $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
                Start-Process -FilePath $taskkill -ArgumentList @('/PID', [string]$script:bootProcess.Id, '/T', '/F') -WindowStyle Hidden -Wait
                $script:bootProcess.WaitForExit()
            }
        }
    } finally {
        $script:bootProcess.Dispose()
        $script:bootProcess = $null
    }
}

function Get-B220PcKey($eventArgs) {
    $code = [int]$eventArgs.KeyCode
    $fixed = @{112='soft_left';113='soft_right';114='call_2';115='call_3';116='call_1';13='ok';38='up';40='down';37='left';39='right';27='end';35='end';106='star';111='hash'}
    if ($fixed.ContainsKey($code)) { return $fixed[$code] }
    if ($eventArgs.Shift -and $code -eq 56) { return 'star' }
    if ($eventArgs.Shift -and $code -eq 51) { return 'hash' }
    if ($code -ge 48 -and $code -le 57) { return [string]($code-48) }
    if ($code -ge 96 -and $code -le 105) { return [string]($code-96) }
    return $null
}

$form.Add_KeyDown({ param($sender,$eventArgs)
    $name = Get-B220PcKey $eventArgs
    if ($name -and $script:sessionReady) {
        $code = [int]$eventArgs.KeyCode
        if (-not $script:pcKeys.ContainsKey($code)) {
            $script:pcKeys[$code] = $name
            Send-B220Key $name $true ('pc:'+$code)
        }
        $eventArgs.SuppressKeyPress = $true
    }
})
$form.Add_KeyUp({ param($sender,$eventArgs)
    $code = [int]$eventArgs.KeyCode
    if ($script:pcKeys.ContainsKey($code)) {
        Send-B220Key $script:pcKeys[$code] $false ('pc:'+$code)
        $script:pcKeys.Remove($code)
        $eventArgs.SuppressKeyPress = $true
    }
})
$form.Add_Deactivate({ Release-B220Keys })
foreach ($control in @($form.Controls)+@($phonePanel.Controls)) {
    $control.Add_PreviewKeyDown({ param($sender,$eventArgs)
        if ($eventArgs.KeyCode -in @('Up','Down','Left','Right')) { $eventArgs.IsInputKey = $true }
    })
}

function Invoke-B220TestPcKey([System.Windows.Forms.Keys]$code) {
    foreach ($name in @('OnKeyDown','OnKeyUp')) {
        $eventArgs = New-Object System.Windows.Forms.KeyEventArgs($code)
        $method = [System.Windows.Forms.Form].GetMethod($name,([System.Reflection.BindingFlags]'Instance,NonPublic'))
        [void]$method.Invoke($script:form,([object[]]@($eventArgs.PSObject.BaseObject)))
    }
}

function Get-B220TestFrameHash {
    if (-not $script:frame) { return '' }
    $buffer = New-Object System.IO.MemoryStream
    $digest = [System.Security.Cryptography.SHA256]::Create()
    try {
        $script:frame.Save($buffer,[System.Drawing.Imaging.ImageFormat]::Bmp)
        return [BitConverter]::ToString($digest.ComputeHash($buffer.ToArray()))
    } finally { $buffer.Dispose(); $digest.Dispose() }
}

function Test-B220Keys($state) {
    if (-not $script:sessionReady) { return }
    if ($script:keyTestStage -ne 0 -and ([DateTime]::UtcNow-$script:keyTestAt).TotalSeconds -gt 90) { throw 'Timed out waiting for the phone to handle a key.' }
    if (-not $script:keyTestSettled) {
        $script:keyTestSettled = $true
        $script:keyTestStage = -2
        $script:keyTestAt = [DateTime]::UtcNow
        Write-B220Command @{action='resume'}
        return
    }
    if ($script:keyTestStage -eq -2) {
        if (([DateTime]::UtcNow-$script:keyTestAt).TotalSeconds -ge 8) { $script:keyTestStage = 0 }
        return
    }
    if ($script:keyTestStage -eq 6) {
        if ($state.state -eq 'paused') {
            $script:keyTestPixels = [int]$state.lcd_pixels
            $script:keyTestAt = [DateTime]::UtcNow
            $script:keyTestStage = 7
        }
        return
    }
    if ($script:keyTestStage -eq 7) {
        if (([DateTime]::UtcNow-$script:keyTestAt).TotalSeconds -lt 1) { return }
        if ([int]$state.lcd_pixels -ne $script:keyTestPixels) { throw 'Guest LCD advanced while paused.' }
        $script:pause.PerformClick()
        $script:keyTestAt = [DateTime]::UtcNow
        $script:keyTestStage = 8
        return
    }
    if ($script:keyTestStage -eq 8) {
        if ($state.state -ne 'running' -or ([DateTime]::UtcNow-$script:keyTestAt).TotalSeconds -lt 1) { return }
        $childId = $script:bootProcess.Id
        $script:form.Close()
        if (Get-Process -Id $childId -ErrorAction SilentlyContinue) { throw 'Interactive child survived window close.' }
        $check = @{status='PASS';phone_buttons=23;mouse_click_path=$true;pc_key_path=$true;guest_key_events=[int]$state.guest_key_events;menu_navigation_and_dialer=$true;pause_resume=$true;close_stops_child=$true} | ConvertTo-Json -Compress
        [System.IO.File]::WriteAllText((Join-Path $script:outputDirectory 'window-key-check.json'),$check)
        return
    }
    if ($script:keyTestStage -eq 0) {
        $script:keyTestAt = [DateTime]::UtcNow
        $script:keyTestEvents = [int]$state.guest_key_events
        $script:keyTestPixels = [int]$state.lcd_pixels
        $script:phoneButtons['soft_left'].PerformClick()
        $script:keyTestStage = if ($state.window_id -in @('0xd001c','0xd001b')) { -1 } else { 1 }
        return
    }
    if ([int]$state.guest_key_events -lt $script:keyTestEvents+2 -or [int]$state.lcd_pixels -le $script:keyTestPixels -or ([DateTime]::UtcNow-$script:keyTestAt).TotalSeconds -lt 8) { return }
    if ($script:keyTestStage -eq -1) { $script:keyTestStage = 0; return }
    $expectedWindows = @{1='0x2';2='0x2';3='0x1';4='0x5';5='0x5'}
    if ($state.window_id -ne $expectedWindows[$script:keyTestStage]) { return }
    if ($script:keyTestStage -eq 2 -and (Get-B220TestFrameHash) -eq $script:keyTestFrameHash) {
        if (-not $script:keyTestNavigationRetried -and $state.backlight) {
            # The firmware consumes the first key when waking a dark LCD.
            $script:keyTestNavigationRetried = $true
            $script:keyTestEvents = [int]$state.guest_key_events
            $script:keyTestPixels = [int]$state.lcd_pixels
            $script:keyTestAt = [DateTime]::UtcNow
            Invoke-B220TestPcKey 'Right'
        }
        return
    }
    $script:form.Refresh()
    $capture = New-Object System.Drawing.Bitmap($script:form.Width,$script:form.Height)
    try {
        $script:form.DrawToBitmap($capture,(New-Object System.Drawing.Rectangle(0,0,$capture.Width,$capture.Height)))
        $capture.Save((Join-Path $script:outputDirectory ('window-key-stage-'+$script:keyTestStage+'.png')),[System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $capture.Dispose() }
    $script:keyTestEvents = [int]$state.guest_key_events
    $script:keyTestPixels = [int]$state.lcd_pixels
    $script:keyTestAt = [DateTime]::UtcNow
    switch ($script:keyTestStage) {
        1 { $script:keyTestFrameHash = Get-B220TestFrameHash; Invoke-B220TestPcKey 'Right' }
        2 { Invoke-B220TestPcKey 'Escape' }
        3 { $script:phoneButtons['1'].PerformClick() }
        4 { $script:phoneButtons['2'].PerformClick() }
        5 {
            $script:pause.PerformClick()
        }
    }
    $script:keyTestStage++
}

function Test-B220Network($state) {
    if (([DateTime]::UtcNow-$script:bootStarted).TotalSeconds -gt 90) { throw 'Network window test timed out.' }
    if (-not $script:sessionReady) { return }
    switch ($script:networkTestStage) {
        0 {
            $script:networkTestStage = 1
            $script:networkButton.PerformClick()
        }
        1 {
            if ($state.network.state -eq 'waiting_browser' -and $script:sessionPaused) { $script:pause.PerformClick() }
            if ($state.network.state -eq 'waiting_browser' -and $state.window_id -eq '0x2' -and -not $script:networkTestBrowserRequested) {
                $script:networkTestBrowserRequested = $true
                $script:phoneButtons['ok'].PerformClick()
            }
            if (-not $state.network.attached) { return }
            if ($state.window_id -ne '0x4001b') {
                if (-not $script:networkTestUrlRequested) {
                    $script:networkTestUrlRequested = $true
                    $script:phoneButtons['ok'].PerformClick()
                }
                return
            }
            $script:networkTestStage = 2
            Show-B220UrlInput
        }
        2 {
            if ($state.network.received_bytes -lt 300 -or ([DateTime]::UtcNow-$script:bootStarted).TotalSeconds -lt 8) { return }
            $script:form.Refresh()
            $capture = New-Object System.Drawing.Bitmap($script:form.Width,$script:form.Height)
            try {
                $script:form.DrawToBitmap($capture,(New-Object System.Drawing.Rectangle(0,0,$capture.Width,$capture.Height)))
                $capture.Save((Join-Path $script:outputDirectory 'window-network.png'),[System.Drawing.Imaging.ImageFormat]::Png)
            } finally { $capture.Dispose() }
            $script:networkTestStage = 3
            $script:networkButton.PerformClick()
        }
        3 {
            if ($state.network.registered -or $state.network.state -eq 'changing') { return }
            $script:networkTestStage = 4
            $script:networkButton.PerformClick()
        }
        4 {
            if ($state.network.state -ne 'off') { return }
            $childId = $script:bootProcess.Id
            $script:networkTestStage = 5
            $script:form.Close()
            if (Get-Process -Id $childId -ErrorAction SilentlyContinue) { throw 'Network child survived window close.' }
            $report = Get-Content -LiteralPath $script:networkReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if (-not ($report.transport.events | Where-Object {$_.kind -eq 'bearer.down'})) { throw 'SIM service did not close its bearer.' }
            $check = @{status='PASS';loopback_only=$true;settings_dialog=$true;url_input=$true;received_bytes=$state.network.received_bytes;unregister=$true;disable=$true;close_stops_child=$true} | ConvertTo-Json
            [System.IO.File]::WriteAllText((Join-Path $script:outputDirectory 'window-network-check.json'),$check)
        }
    }
}

$script:timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 120
$timer.Add_Tick({
    if ($script:bootProcess -and (Test-Path -LiteralPath $script:liveFramePath)) {
        $stamp = [System.IO.File]::GetLastWriteTimeUtc($script:liveFramePath)
        if ($stamp -ge $script:bootStarted -and $stamp -gt $script:liveStamp) {
            try { Set-B220Frame $script:liveFramePath; $script:liveStamp = $stamp }
            catch { } # A transient read race is retried on the next UI tick.
        }
    }
    if ($script:sessionStatusPath -and (Test-Path -LiteralPath $script:sessionStatusPath)) {
        try {
            $state = Get-Content -LiteralPath $script:sessionStatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $script:sessionReady = [bool]$state.ready -and $state.state -in @('paused','running')
            $script:sessionPaused = $state.state -eq 'paused'
            Update-B220Debugger $state
            if ($state.sd_card) {
                $script:tooltip.SetToolTip($script:cardButton,('Карта: '+$(if ($state.sd_card.present) {'вставлена'} else {'нет'})+"`nЧтений: $($state.sd_card.sectors_read), записей: $($state.sd_card.sectors_written)"))
            }
            $script:networkState = $state.network
            if ($state.network) {
                $script:networkReportPath = [string]$state.network.report
                $script:networkJournal.Enabled = Test-Path -LiteralPath $script:networkReportPath
                $networkCaption = switch ([string]$state.network.state) {
                    'off' { 'Интернет выключен' }
                    'waiting_browser' { 'Ожидание браузера' }
                    'changing' { 'Меняется состояние SIM…' }
                    'absent' { 'SIM1 извлечена' }
                    'confirming' { 'Подключение…' }
                    'active' { 'Интернет подключён' }
                    'error' { 'Ошибка сети · перезапустите телефон' }
                    default { if ($state.network.registered) { 'SIM1 готова к подключению' } else { 'SIM1 без регистрации' } }
                }
                if ($state.network.transport_mode -like 'loopback*') { $networkCaption = 'Тест: '+$networkCaption }
                $script:networkLabel.Text = $networkCaption
                if ($state.network.attached) {
                    $script:networkLabel.Text += "`n↑ $($state.network.sent_bytes) Б   ↓ $($state.network.received_bytes) Б"
                }
                $script:tooltip.SetToolTip($script:networkLabel,[string]$state.network.error)
            }
            if ($null -ne $state.backlight -and $script:lcdBacklight -ne [bool]$state.backlight) {
                $script:lcdBacklight = [bool]$state.backlight
                $script:screen.Invalidate()
            }
            $script:phonePanel.Enabled = $script:sessionReady
            $script:pause.Enabled = $state.state -in @('paused','running','booting')
            if ($script:sessionReady) {
                $script:restart.Enabled = $true
                $script:journal.Enabled = Test-Path -LiteralPath $script:reportPath
                $script:pause.Text = if ($script:sessionPaused) { 'Продолжить' } else { 'Пауза' }
                $script:status.Text = if ($script:sessionPaused) { 'Готов к нажатиям · пауза' } else { 'Телефон работает' }
                $script:detail.Text = 'Мышь или клавиатура: стрелки, Enter, цифры, F1/F2.'
                if (-not $script:lcdBacklight) {
                    $script:status.Text = 'Подсветка выключена прошивкой'
                    $script:detail.Text = 'Первое нажатие включает подсветку. Затем нажмите нужную клавишу.'
                }
                if ($state.input_error) { $script:detail.Text = [string]$state.input_error }
                if ($state.network.error) { $script:detail.Text = [string]$state.network.error }
                $script:progress.Visible = $false
            }
            if ($state.state -eq 'paused' -and -not $script:sessionReady) {
                $script:restart.Enabled = $true
                $script:pause.Text = 'Продолжить'
                $script:status.Text = 'CPU остановлен · загрузка не завершена'
                $script:detail.Text = 'Регистры и инструкции доступны в окне «Отладка…».'
                $script:progress.Visible = $false
            } elseif (-not $script:sessionReady -and $state.state -eq 'booting') {
                $script:pause.Text = 'Пауза'
                $script:status.Text = 'Выполняется загрузка прошивки…'
                $script:progress.Visible = $true
            }
        } catch { $state = $null }
        if ($DebuggerTest -and $state -and $state.state -eq 'paused' -and -not $script:debugTestStarted) {
            $script:debugTestStarted = $true
            $script:debugButton.PerformClick()
            $childId = $script:bootProcess.Id
            $script:form.Close()
            if (Get-Process -Id $childId -ErrorAction SilentlyContinue) { throw 'Debugger child survived window close.' }
            return
        }
        if ($KeyTest -and $state) {
            try { Test-B220Keys $state }
            catch { Show-B220Error $_.Exception.Message }
        }
        if ($NetworkTest -and $state) {
            try { Test-B220Network $state }
            catch { Show-B220Error $_.Exception.Message }
        }
        if ($CardTest -and $script:sessionReady -and -not $script:cardTestDone) {
            try {
                $script:cardTestDone = $true
                if (-not $state.sd_card.present) { throw 'No SD card in the test snapshot.' }
                $script:form.Refresh()
                $capture = New-Object System.Drawing.Bitmap($script:form.Width,$script:form.Height)
                try {
                    $script:form.DrawToBitmap($capture,(New-Object System.Drawing.Rectangle(0,0,$capture.Width,$capture.Height)))
                    $capture.Save((Join-Path $script:outputDirectory 'window-card.png'),[System.Drawing.Imaging.ImageFormat]::Png)
                } finally { $capture.Dispose() }
                $script:cardButton.PerformClick()
                $childId = $script:bootProcess.Id
                $script:form.Close()
                if (Get-Process -Id $childId -ErrorAction SilentlyContinue) { throw 'SD child survived window close.' }
                [System.IO.File]::WriteAllText((Join-Path $script:outputDirectory 'window-card-check.json'),'{"status":"PASS","sd_attached":true,"settings_dialog":true,"close_stops_child":true}')
            } catch { Show-B220Error $_.Exception.Message }
        }
    }
    if (-not $script:bootProcess -or -not $script:bootProcess.HasExited) { return }
    $script:timer.Stop()
    $script:sessionReady = $false
    $script:phonePanel.Enabled = $false
    $script:pause.Enabled = $false
    $finishedProcess = $script:bootProcess
    $script:bootProcess = $null
    try {
        $finishedProcess.WaitForExit()
        if ($finishedProcess.ExitCode -ne 0) {
            $failure = Get-Content -LiteralPath $script:stderrPath -Raw -Encoding UTF8
            throw ('Процесс эмулятора завершился с ошибкой.' + [Environment]::NewLine + $failure)
        }
        $script:report = Get-Content -LiteralPath $script:reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $script:progress.Visible = $false
        $script:restart.Enabled = $true
        $script:journal.Enabled = $true
        if ($script:report.lcd_image) {
            $script:framePath = [string]$script:report.lcd_image
            if (-not [System.IO.Path]::IsPathRooted($script:framePath)) {
                $script:framePath = Join-Path $script:projectRoot $script:framePath
            }
            Set-B220Frame $script:framePath
        }
        if ($script:report.stop.reason -in @('wall time budget', 'instruction budget', 'requested end address')) {
            $script:status.Text = 'Пробный запуск завершён'
            $script:detail.Text = 'Достигнут предел запуска. Показан последний кадр; подробности — в журнале.'
        } elseif ($script:report.stop.reason -eq 'requested LCD frame count') {
            $script:status.Text = 'Экран интерфейса — контрольная точка'
            $script:detail.Text = 'Загрузка дошла до интерфейса телефона. Ввод клавиш пока не подключён.'
        } elseif ($script:report.stop.device -eq 'JPEG engine') {
            $script:status.Text = 'Остановка: неподдерживаемый режим JPEG'
            $script:detail.Text = 'Базовый декодер работает. Этот режим обработки изображения ещё не реализован.'
        } elseif ($script:report.stop.reason -in @('Internet emulation boundary','virtual network boundary','loopback network boundary')) {
            $script:status.Text = 'Остановка сетевого эксперимента'
            $script:detail.Text = [string]$script:report.stop.detail
            if (-not $script:detail.Text) { $script:detail.Text = [string]$script:report.stop.operation }
        } else {
            $script:status.Text = 'Выполнение остановлено'
            $script:detail.Text = 'Достигнута другая точка остановки. Подробности — в журнале запуска.'
        }
        if ($KeyTest -or $NetworkTest) { throw ('Interactive window test stopped early: '+$script:report.stop.reason) }
        if ($ScreenTest) {
            if (-not $script:frame -or $script:report.stop.reason -ne 'requested LCD frame count' -or $script:report.full_lcd_frames -ne 5 -or $script:report.jpeg.operations.Count -lt 1) { throw 'GUI did not reach MMI through the JPEG model.' }
            $script:form.Refresh()
            $capture = New-Object System.Drawing.Bitmap($script:form.Width, $script:form.Height)
            try {
                $script:form.DrawToBitmap($capture, (New-Object System.Drawing.Rectangle(0, 0, $capture.Width, $capture.Height)))
                $capture.Save((Join-Path $script:outputDirectory 'window-screen-test.png'), [System.Drawing.Imaging.ImageFormat]::Png)
            } finally { $capture.Dispose() }
            [System.IO.File]::WriteAllText((Join-Path $script:outputDirectory 'window-screen-check.json'), '{"status":"PASS","full_boot":true,"jpeg":true,"lcd_frames":5,"mmi_screen":true}')
            $script:form.Close()
        } elseif ($SmokeTest) {
            if (-not $script:frame -or $script:report.stop.pc -ne '0x1a0fc2' -or $script:liveStamp -eq [DateTime]::MinValue) { throw 'GUI smoke check did not reach MMI with a live LCD frame.' }
            if ($script:smokeRestarts -eq 0) {
                $script:smokeRestarts++
                $script:zoom.SelectedIndex = 0
                $script:zoom.SelectedIndex = [Math]::Min(1, $script:zoom.Items.Count - 1)
                $script:restart.PerformClick()
                return
            }
            $script:form.Refresh()
            $capture = New-Object System.Drawing.Bitmap($script:form.Width, $script:form.Height)
            try {
                $script:form.DrawToBitmap($capture, (New-Object System.Drawing.Rectangle(0, 0, $capture.Width, $capture.Height)))
                $capture.Save((Join-Path $script:outputDirectory 'window-smoke.png'), [System.Drawing.Imaging.ImageFormat]::Png)
            } finally { $capture.Dispose() }
            # Also verify that closing the window cancels an active child boot.
            $script:restart.PerformClick()
            if (-not $script:bootProcess -or $script:bootProcess.HasExited) { throw 'Close-during-boot check did not start a child process.' }
            $childToClose = $script:bootProcess.Id
            $script:form.Close()
            if (Get-Process -Id $childToClose -ErrorAction SilentlyContinue) { throw 'Child process survived GUI close.' }
            [System.IO.File]::WriteAllText((Join-Path $script:outputDirectory 'window-smoke.json'), '{"completed_boots":2,"restart":true,"zoom":true,"live_frames":true,"close_during_boot":true,"status":"PASS"}')
        }
    } catch {
        Show-B220Error $_.Exception.Message
    } finally {
        $finishedProcess.Dispose()
    }
})

$form.Add_Shown({ Start-B220Boot })
$form.Add_FormClosing({
    Stop-B220Child
})

try {
    $zoom.SelectedIndex = [Math]::Min(1, $zoom.Items.Count - 1)
    [void]$form.ShowDialog()
} finally {
    $timer.Dispose()
    $tooltip.Dispose()
    if ($script:bootProcess) { $script:bootProcess.Dispose() }
    if ($script:frame) { $script:frame.Dispose() }
    $form.Dispose()
}
if ($script:smokeFailure) { Write-Error $script:smokeFailure; exit 1 }
