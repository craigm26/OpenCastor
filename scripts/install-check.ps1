#Requires -Version 5.1
# OpenCastor Post-Install Verification (Windows)

$pass = 0; $fail = 0
function Test-Check($name, [scriptblock]$test) {
    try {
        $null = & $test
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "nonzero" }
        Write-Host "  ✅ $name" -ForegroundColor Green
        $script:pass++
    } catch {
        Write-Host "  ❌ $name" -ForegroundColor Red
        $script:fail++
    }
}

Write-Host "`nOpenCastor Install Check`n========================"

$py = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" }
      elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" }
      else { $null }

Test-Check "Python found" { if (-not $py) { throw "missing" } }
Test-Check "Python 3.10+" { & $py -c "import sys; assert sys.version_info >= (3,10)" }
Test-Check "pip available" { & $py -m pip --version }
Test-Check "venv module" { & $py -c "import venv" }
Test-Check "git installed" { git --version }

foreach ($mod in @("cv2", "numpy", "pydantic", "yaml")) {
    Test-Check "import $mod" { & $py -c "import $mod" }
}

Test-Check "castor CLI" { & $py -m castor --help }

Write-Host "`nResults: $pass passed, $fail failed"
if ($fail -eq 0) { Write-Host "🎉 All checks passed!" -ForegroundColor Green }
else { Write-Host "⚠️  Some checks failed." -ForegroundColor Yellow }
exit $fail
