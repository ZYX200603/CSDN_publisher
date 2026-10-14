# 参与贡献 / Contributing

> 中文在上，English below.

## 中文

### 开始之前

1. 本项目的铁律都写在 `skills/csdn-publish/SKILL.md（文首「第 0 条」）和 `log/known-issues.md` —— **动手前先读这两份**。里面每条结论都是实测踩出来的，改代码时不要改掉。
2. 提交前跑离线自测（不开浏览器、不联网）：

   ```powershell
   .\.venv\Scripts\python.exe test_robustness.py
   .\.venv\Scripts\python.exe test_launch_paths.py
   .\.venv\Scripts\python.exe test_category_gate.py
   .\.venv\Scripts\python.exe test_schedule.py
   ```

   改了 `risk` / `crash` / `browser` / 排期相关代码，前两套必跑。

### 必须遵守的约定

1. **选择器、接口地址、阈值一律进 `config.json`，不改代码。** CSDN 改版时优先改配置。
2. **绝不为某一天/某一批新建脚本副本。** 复盘这类「一天一参数」的需求，用「一个脚本 + 命令行参数」解决（见 `csdn_retro.py` / `csdn_rank.py` 的分工）。
3. **`.bat` 文件保持 CRLF 行尾**（由 `.gitattributes` 强制）；源码与文档统一 LF。
4. **不要提交任何敏感信息**：`.profile/`（登录态 cookie）、账号 ID、token 一律不进仓库；拿不准的内容先脱敏成 `<占位符>`。
5. **写入 → 回读 → 比对 → 不一致就中止**：凡是发出去后要能被外部系统证实的字段（栏目、标签、定时时间、封面），都要有这个闭环（见 `log/known-issues.md` 第 11 条）。
6. **「解析失败就当没数据」的静默降级不许有**：宁可抛错，也不要默默返回 None。
7. 文档（`README*.md` / `skills/csdn-publish/*.md`）里的命令必须真实可执行；改行为先改文档。

### 提交信息

简短说明「做了什么 + 为什么」，例如：`fix(message): 发送判定改用本次正文标记做主判据`。

---

## English

### Before you start

1. The project's hard rules live in `skills/csdn-publish/SKILL.md (Rule 0 at the top)` and
   `log/known-issues.md` — read both first. Every entry there was learned the hard way.
2. Run the offline self-tests before submitting (no browser, no network): see the four
   `test_*.py` commands above. The first two are mandatory after touching
   `risk` / `crash` / `browser` / scheduling code.

### Conventions

1. **Selectors, endpoints and thresholds belong in `config.json`, not in code.**
2. **Never clone a script for a specific day/batch.** "One script + CLI parameters" is the
   rule (see the `csdn_retro.py` / `csdn_rank.py` split).
3. **Keep `.bat` files CRLF** (enforced via `.gitattributes`); source and docs are LF.
4. **Never commit secrets**: `.profile/` (login cookies), account IDs, tokens. When in
   doubt, sanitize to `<placeholder>`.
5. **Write → read back → compare → abort on mismatch** for anything the platform must
   confirm (column, tags, schedule time, cover).
6. **No silent degradation** ("parse failed → return None"). Raise instead.
7. Commands in the docs must be real and runnable; change docs before behavior.

### Commit messages

Short "what + why", e.g. `fix(message): use this-run body marker as primary sent criterion`.
