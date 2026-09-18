param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

& python "$PSScriptRoot\..\run_cli.py" @ScriptArgs
