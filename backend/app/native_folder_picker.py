"""Read-only Windows folder selection; paths are returned, never granted for writes."""
import os
from pathlib import Path
import subprocess

from app.local_filesystem import LocalFilesError


def select_native_directory(description, initial_path=""):
    if os.name != "nt": raise LocalFilesError("LOCAL_PICKER_UNAVAILABLE")
    # Values travel as environment variables, never as interpolated shell code.
    script = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description=$env:REAWOTE_PICKER_DESCRIPTION; $d.SelectedPath=$env:REAWOTE_PICKER_ROOT; $d.ShowNewFolderButton=$false; if($d.ShowDialog() -eq 'OK'){[Console]::Write($d.SelectedPath)}"
    try:
        result = subprocess.run([str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile", "-STA", "-Command", script],
            env={**os.environ, "REAWOTE_PICKER_ROOT": str(initial_path), "REAWOTE_PICKER_DESCRIPTION": description},
            capture_output=True, text=True, encoding="utf-8", timeout=180, creationflags=0x08000000)
        if result.returncode: raise LocalFilesError("LOCAL_PICKER_UNAVAILABLE")
        return result.stdout.strip() or None
    except subprocess.TimeoutExpired: raise LocalFilesError("LOCAL_PICKER_TIMEOUT") from None
    except (OSError, KeyError): raise LocalFilesError("LOCAL_PICKER_UNAVAILABLE") from None
