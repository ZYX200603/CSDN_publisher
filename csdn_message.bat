@echo off
chcp 65001 >nul
REM CSDN 私信群发启动器（给「我关注的人 / 我的粉丝」发同一条私信）
REM 正文来自 templates\message.txt（纯文本，原样发送）
REM 名单走 CSDN 名单接口一次取全，不再受列表页 20 条上限（--limit all = 全部）
REM 用法:
REM   csdn_message.bat --to follower --limit 20 --wait       只列名单，不发送
REM   csdn_message.bat --to follower --limit all --wait      全部粉丝的名单
REM   csdn_message.bat --to follower --limit all --yes       给全部粉丝真实发送
REM   csdn_message.bat --to following,follower --limit all --wait
REM   csdn_message.bat --to follower --limit all --yes --skip-sent   跳过"以前发过的人"（补发失败的那批）
REM   csdn_message.bat --to follower --limit 1 --dry-run     只演练选择器，不发送
REM   csdn_message.bat --stats                               看历史发送记录
"%~dp0.venv\Scripts\python.exe" "%~dp0csdn_message.py" %*
