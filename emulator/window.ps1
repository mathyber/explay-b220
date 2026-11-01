param([switch]$SmokeTest, [switch]$ScreenTest, [switch]$KeyTest)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$script:projectRoot = Split-Path -Parent $PSScriptRoot
$script:pythonPath = Join-Path $script:projectRoot '.venv\Scripts\python.exe'
$script:outputDirectory = Join-Path $script:projectRoot 'analysis\emulator'
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
$maxScale = [Math]::Max(1, [Math]::Min(4, [Math]::Floor(([System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Height - 500) / 220)))
for ($scaleOption = 1; $scaleOption -le $maxScale; $scaleOption++) { [void]$zoom.Items.Add("${scaleOption}×") }
$zoom.Add_SelectedIndexChanged({ Set-B220Layout })
$form.Controls.Add($zoom)

function Set-B220Layout {
    $scale = $script:zoom.SelectedIndex + 1
    $width = [Math]::Max(400, 176 * $scale + 48)
    $height = 220 * $scale + 459
    $script:form.ClientSize = New-Object System.Drawing.Size($width, $height)
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
    $area = [System.Windows.Forms.Screen]::FromControl($script:form).WorkingArea
    if ($script:form.Bottom -gt $area.Bottom) { $script:form.Top = [Math]::Max($area.Top, $area.Bottom - $script:form.Height) }
}

function Show-B220Error([string]$message) {
    $script:progress.Visible = $false
    $script:restart.Enabled = $true
    $script:status.Text = 'Не удалось завершить запуск'
    $script:detail.Text = 'Можно повторить запуск. Подробности — в сообщении об ошибке.'
    if ($SmokeTest -or $ScreenTest -or $KeyTest) {
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
        if (-not (Test-Path -LiteralPath $script:pythonPath)) { throw 'Не найден Python проекта в .venv. Подготовьте окружение по README.md.' }
        $script:restart.Enabled = $false
        $script:save.Enabled = $false
        $script:journal.Enabled = $false
        $script:report = $null
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
        else {
            if (-not $ScreenTest -and -not $KeyTest) { $argsForBoot += @('--save-flash', ('"' + $script:virtualNorPath + '"')) }
            if (Test-Path -LiteralPath $script:virtualNorPath) { $argsForBoot += @('--load-flash', ('"' + $script:virtualNorPath + '"')) }
        }
        $script:bootProcess = Start-Process -FilePath $script:pythonPath -ArgumentList $argsForBoot -WorkingDirectory $script:projectRoot -WindowStyle Hidden -RedirectStandardOutput $script:stdoutPath -RedirectStandardError $script:stderrPath -PassThru
        # Keep the process handle open so ExitCode remains available after exit.
        [void]$script:bootProcess.Handle
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
            if ($null -ne $state.backlight -and $script:lcdBacklight -ne [bool]$state.backlight) {
                $script:lcdBacklight = [bool]$state.backlight
                $script:screen.Invalidate()
            }
            $script:phonePanel.Enabled = $script:sessionReady
            $script:pause.Enabled = $script:sessionReady
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
                $script:progress.Visible = $false
            }
        } catch { $state = $null }
        if ($KeyTest -and $state) {
            try { Test-B220Keys $state }
            catch { Show-B220Error $_.Exception.Message }
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
        } else {
            $script:status.Text = 'Выполнение остановлено'
            $script:detail.Text = 'Достигнута другая точка остановки. Подробности — в журнале запуска.'
        }
        if ($KeyTest) { throw ('Interactive key test stopped early: '+$script:report.stop.reason) }
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
