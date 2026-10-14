@echo off
chcp 65001 >nul
REM CSDN 一键三连（点赞/收藏/评论）启动器
REM 用法:
REM   csdn_interact.bat --ops like --count 50
REM   csdn_interact.bat --ops like,comment,save --count 50
REM   csdn_interact.bat --ops 三连 --count 50
REM   csdn_interact.bat --stats
REM   csdn_interact.bat --probe-comment <文章URL>
"%~dp0.venv\Scripts\python.exe" "%~dp0csdn_interact.py" %*
