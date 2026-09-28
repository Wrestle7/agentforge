# 独立公开仓库

本目录独立管理 Git 历史、依赖示例和远程仓库。新克隆按 README 创建仓库内 `.venv`；原开发工作区的父目录环境仍可使用，不自动迁移或删除。

公开内容包括源码、样例、测试、评测脚本和实际报告。`.env`、虚拟环境、数据库、模型缓存、运行日志和交付压缩包不提交。历史评测 `reports/deepseek-v2/source.zip` 是该批实验的源码快照，作为证据保留。

公开报告移除了本机用户名、主机名和 LangSmith 私人工作区链接；测试数量、评测分数和失败记录保持原样。本机原始报告备份在忽略的 `data/private-publish-backup`。配置示例中的数据库密码只用于本地演示。

后续更新前运行：

```powershell
git add .
.\.venv\Scripts\python.exe scripts/check_public.py
git diff --cached --stat
git commit -m "Describe the change"
git push
```

发布检查读取 Git 暂存区，核对私有配置中的实际密钥、常见凭据格式、禁止提交的路径，以及嵌套源码压缩包。它是本项目的检查，不声称覆盖所有秘密格式。
