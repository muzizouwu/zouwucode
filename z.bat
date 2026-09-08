@echo off
REM ============================================================================
REM  ZOUWUCODE Quick Launch Script
REM  Usage: Place this script in a PATH directory (or a PATH'd clone root),
REM  then type "z" in any terminal.
REM
REM  The project root is derived from this script's own location, so it works
REM  from any clone location (no hardcoded paths).
REM ============================================================================

set "ZOUWUCODE_DIR=%~dp0"
set ZOUWUCODE_PORTABLE=1

cd /d "%ZOUWUCODE_DIR%"
python -m zouwucode %*

exit /b %ERRORLEVEL%
